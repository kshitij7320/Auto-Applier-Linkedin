import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db import DatabaseTracker
from src.stealth import connect_to_cdp, StealthController
from src.solver import ScreeningSolver
from src.modal_navigator import ModalNavigator, NavigationResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("LinkedInAutoApplier")

class ApplicationRunner:
    def __init__(self, config_dir: str = "config", db_path: str = "storage/tracker.db"):
        settings_path = os.path.join(config_dir, "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            self.settings: Dict[str, Any] = json.load(f)

        self.profile_path = os.path.join(config_dir, "profile.json")
        self.db = DatabaseTracker(db_path)
        self.db.init_db()
        self.solver = ScreeningSolver(
            profile_path=self.profile_path,
            db_tracker=self.db
        )
        self.max_applications = int(self.settings.get("max_applications_per_session", 20))

    async def run(self) -> Dict[str, int]:
        cdp_url = self.settings.get("cdp_url", "http://localhost:9222")
        logger.info(f"Connecting to CDP instance at {cdp_url} (Zero Headless Mode)...")

        pw, browser, page = await connect_to_cdp(cdp_url)
        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=self.solver,
            max_steps=self.settings.get("max_steps_per_modal", 8),
            confidence_threshold=self.settings.get("confidence_threshold", 0.85)
        )

        stats = {"applied": 0, "discarded": 0, "failed": 0, "skipped": 0}
        consecutive_errors = 0

        try:
            search_url = self.settings.get("search_url")
            logger.info(f"Navigating to job search URL: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded")
            await stealth.random_delay(3.0, 5.0)

            # Check if user is logged into LinkedIn
            login_form = page.locator("input#username, input#session_key, a.nav__button-secondary:has-text('Sign in')")
            if await login_form.count() > 0:
                logger.warning("LinkedIn is currently in a logged-out or authwall state.")
                logger.info("Please complete sign-in in the open Chrome window so Easy Apply is enabled.")

            while stats["applied"] < self.max_applications and consecutive_errors < 3:
                # Find all job cards on current page (supports both authenticated and public layouts)
                job_cards = page.locator(".job-card-container, [data-occludable-job-id], ul.jobs-search__results-list li, .base-card, .job-search-card")
                count = await job_cards.count()
                logger.info(f"Found {count} job cards on current search page.")

                if count == 0:
                    logger.warning("No job cards found. Ensure you are signed in and search results have loaded.")
                    break

                for i in range(count):
                    if stats["applied"] >= self.max_applications:
                        logger.info(f"Reached hard application limit of {self.max_applications}.")
                        break

                    card = job_cards.nth(i)
                    job_id = (
                        await card.get_attribute("data-job-id")
                        or await card.get_attribute("data-occludable-job-id")
                        or f"card_{i}"
                    )

                    # 1. Duplicate suppression
                    if self.db.is_already_applied(job_id):
                        logger.info(f"Skipping job_id {job_id} (already applied in database).")
                        stats["skipped"] += 1
                        continue

                    # Extract card details
                    title_elem = card.locator(".job-card-list__title, a.job-card-container__link").first
                    title = (await title_elem.inner_text()).strip() if await title_elem.count() > 0 else "Unknown Title"
                    company_elem = card.locator(".job-card-container__primary-description").first
                    company = (await company_elem.inner_text()).strip() if await company_elem.count() > 0 else "Unknown Company"

                    logger.info(f"Evaluating card [{i+1}/{count}]: '{title}' at '{company}' (ID: {job_id})")

                    # Click card using Bézier movement
                    await stealth.click_element(card)
                    await stealth.random_delay(1.5, 3.0)

                    # Check for Easy Apply button
                    easy_apply_btn = page.locator(".jobs-apply-button--top-card button.jobs-apply-button").first
                    if await easy_apply_btn.count() == 0 or "easy apply" not in (await easy_apply_btn.inner_text()).lower():
                        logger.info(f"Job {job_id} does not have an Easy Apply button. Skipping.")
                        stats["skipped"] += 1
                        continue

                    # Trigger Easy Apply
                    logger.info(f"Triggering Easy Apply for '{title}'...")
                    await stealth.click_element(easy_apply_btn)
                    await stealth.random_delay(1.5, 2.5)

                    # Run Modal State Machine
                    result, reason = await navigator.process_easy_apply_modal()

                    if result == NavigationResult.SUBMITTED:
                        logger.info(f"Successfully applied to {title} ({job_id})!")
                        self.db.record_application(job_id, title, company, "N/A", "APPLIED")
                        stats["applied"] += 1
                        consecutive_errors = 0
                    elif result == NavigationResult.DISCARDED:
                        logger.warning(f"Discarded application for {job_id}: {reason}")
                        self.db.record_application(job_id, title, company, "N/A", "DISCARDED", reason)
                        stats["discarded"] += 1
                    else:
                        logger.error(f"Failed application for {job_id}: {reason}")
                        self.db.record_application(job_id, title, company, "N/A", "FAILED", reason)
                        stats["failed"] += 1
                        consecutive_errors += 1

                    # Organic delay between applications
                    inter_delay = float(self.settings.get("inter_job_delay_min_seconds", 12.0))
                    logger.info(f"Waiting {inter_delay:.1f}s before next card evaluation...")
                    await asyncio.sleep(inter_delay)

                # Pagination: try to click next page if quota remaining
                if stats["applied"] < self.max_applications:
                    next_page = page.locator("button[aria-label*='Page '].active + button, button[aria-label='Next']").first
                    if await next_page.count() > 0:
                        logger.info("Advancing to next search page...")
                        await stealth.click_element(next_page)
                        await stealth.random_delay(4.0, 7.0)
                    else:
                        logger.info("No further pages available.")
                        break

        finally:
            try:
                await pw.stop()
            except Exception:
                pass

        logger.info(f"Session completed: {stats}")
        return stats

def main():
    runner = ApplicationRunner()
    asyncio.run(runner.run())

if __name__ == "__main__":
    main()
