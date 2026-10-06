import asyncio
import json
import logging
import os
import random
import sys
from typing import Dict, Any
from urllib.parse import urlencode

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from src.db import DatabaseTracker
from src.job_page import (
    find_easy_apply_button,
    is_auth_wall,
    job_marked_applied,
    job_title_link,
    load_more_results,
    card_already_applied,
    posted_within_hours,
    read_card_company,
    read_card_posted_text,
    read_card_text,
    read_card_title,
    read_details_title,
    read_job_id,
    title_priority,
    title_rejection_reason,
    open_job_matches,
    wait_for_easy_apply_modal,
    wait_for_selected_job,
)
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
        self.question_bank_path = os.path.join("storage", "collected_questions.json")
        self.solver = ScreeningSolver(
            profile_path=self.profile_path,
            db_tracker=self.db,
            question_bank_path=self.question_bank_path,
        )
        self.solver.question_bank.seed(self.solver.preview_answer, refresh=True)
        self.solver.question_bank.refresh_saved(self.solver.preview_answer)
        self.max_applications = int(self.settings.get("max_applications_per_session", 20))
        self.max_post_age_hours = int(self.settings.get("max_post_age_hours", 24))
        self.excluded_stacks = self.settings.get("excluded_stacks", [])
        self.excluded_titles = [t.lower() for t in self.settings.get("excluded_titles", [])]
        experience = self.solver.profile.get("experience", {})
        self.priority_skills = experience.get(
            "primary_skills", ["JavaScript", "React.js", "Express.js", "Node.js", "MongoDB"]
        )
        excluded_names = {item.lower() for item in self.excluded_stacks}
        self.secondary_skills = [
            skill for skill in experience.get("secondary_skills", [])
            if skill.lower() not in excluded_names
        ]
        self.active_title_terms = ["javascript", "react", "express", "node", "mongo", "mern"]

    def _search_url(self, keywords: str) -> str:
        return "https://www.linkedin.com/jobs/search/?" + urlencode({
            "f_AL": "true",
            "f_E": "1,2",
            "f_TPR": "r86400",
            "keywords": keywords,
        })

    async def _consider_job(self, page, stealth, navigator, card, job_id: str, stats: Dict[str, int]) -> bool:
        try:
            await card.scroll_into_view_if_needed(timeout=4000)
            await page.wait_for_timeout(250)
        except Exception:
            pass
        title = await read_card_title(card)
        company = await read_card_company(card)
        logger.info(f"Evaluating '{title}' at '{company}' (ID: {job_id})")

        card_text = await read_card_text(card)
        rejection = title_rejection_reason(
            title,
            required_terms=self.active_title_terms,
            excluded_stacks=self.excluded_stacks,
            excluded_seniority=self.excluded_titles,
        ) or title_rejection_reason(
            card_text,
            required_terms=[],
            excluded_stacks=self.excluded_stacks,
            excluded_seniority=[],
        )
        if rejection in {"seniority", "stack", "role"}:
            stats["skipped"] += 1
            stats["skipped_off_stack"] = stats.get("skipped_off_stack", 0) + 1
            return False

        age_text = await read_card_posted_text(card)
        recent = posted_within_hours(age_text, self.max_post_age_hours)
        if recent is False:
            logger.info(f"Skipping job '{title}' (posted outside the last {self.max_post_age_hours} hours).")
            stats["skipped"] += 1
            return False

        if self.db.is_already_applied(job_id) or await card_already_applied(card):
            logger.info(f"Skipping job_id {job_id} (already applied).")
            if not self.db.is_already_applied(job_id):
                self.db.record_application(job_id, title, company, "N/A", "APPLIED")
            stats["skipped"] += 1
            return False

        link = await job_title_link(card)
        await stealth.click_element(link)
        await stealth.random_delay(1.0, 2.0)

        opened = await wait_for_selected_job(page, job_id, title) or await open_job_matches(page, job_id, title)
        if not opened:
            try:
                await link.click(timeout=4000)
                await stealth.random_delay(0.6, 1.2)
            except Exception:
                pass
            opened = await wait_for_selected_job(page, job_id, title) or await open_job_matches(page, job_id, title)
        if not opened:
            logger.info(f"Details pane did not switch to job {job_id}. Skipping.")
            stats["skipped"] += 1
            return False

        details_title = await read_details_title(page)
        details_rejection = title_rejection_reason(
            details_title or title,
            required_terms=self.active_title_terms,
            excluded_stacks=self.excluded_stacks,
            excluded_seniority=self.excluded_titles,
        )
        if details_rejection:
            logger.info(f"Skipping job '{details_title or title}' (details pane is outside the target stack).")
            stats["skipped"] += 1
            return False

        if await job_marked_applied(page):
            logger.info(f"LinkedIn already shows job {job_id} as applied.")
            self.db.record_application(job_id, title, company, "N/A", "APPLIED")
            stats["skipped"] += 1
            return False

        easy_apply_btn = await find_easy_apply_button(page)
        if easy_apply_btn is None:
            logger.info(f"Job {job_id} has no visible Easy Apply button in the details pane. Skipping.")
            stats["skipped"] += 1
            return False

        self.solver.current_job_title = title
        logger.info(f"Triggering Easy Apply for '{title}'...")
        await stealth.click_element(easy_apply_btn)
        if not await wait_for_easy_apply_modal(page):
            try:
                await easy_apply_btn.click(timeout=4000)
            except Exception:
                pass
            if not await wait_for_easy_apply_modal(page):
                logger.error(f"Easy Apply modal did not open for {job_id}.")
                self.db.record_application(job_id, title, company, "N/A", "FAILED", "Easy Apply modal did not open")
                stats["failed"] += 1
                return True

        try:
            result, reason = await navigator.process_easy_apply_modal()
        except Exception as e:
            logger.exception(f"Unexpected error processing modal for {job_id}: {e}")
            await navigator.discard_modal()
            result = NavigationResult.FAILED
            reason = f"Exception: {e}"

        if result == NavigationResult.SUBMITTED:
            logger.info(f"Successfully applied to {title} ({job_id})!")
            self.db.record_application(job_id, title, company, "N/A", "APPLIED")
            stats["applied"] += 1
        elif result == NavigationResult.DISCARDED:
            logger.warning(f"Discarded application for {job_id}: {reason}")
            self.db.record_application(job_id, title, company, "N/A", "DISCARDED", reason)
            stats["discarded"] += 1
        else:
            logger.error(f"Failed application for {job_id}: {reason}")
            self.db.record_application(job_id, title, company, "N/A", "FAILED", reason)
            stats["failed"] += 1

        delay_min = float(self.settings.get("inter_job_delay_min_seconds", 12.0))
        delay_max = float(self.settings.get("inter_job_delay_max_seconds", delay_min))
        inter_delay = random.uniform(delay_min, max(delay_min, delay_max))
        logger.info(f"Waiting {inter_delay:.1f}s before the next application...")
        await asyncio.sleep(inter_delay)
        return True

    async def _drain_search(self, page, stealth, navigator, seen_job_ids, stats) -> int:
        matched = 0
        consecutive_errors = 0
        while stats["applied"] < self.max_applications and consecutive_errors < 3:
            if await is_auth_wall(page):
                logger.error("LinkedIn auth wall appeared mid-session. Stopping.")
                break

            job_cards = page.locator("li[data-occludable-job-id]")
            count = await job_cards.count()
            logger.info(f"Found {count} job cards on the current results list.")
            if count == 0:
                logger.warning("No job cards found. Sign in and wait until search results load.")
                break

            job_ids = []
            for i in range(count):
                try:
                    job_id = await read_job_id(job_cards.nth(i))
                except Exception:
                    continue
                if job_id and job_id not in seen_job_ids and job_id not in job_ids:
                    job_ids.append(job_id)

            ranked = []
            for job_id in job_ids:
                card = page.locator(f"li[data-occludable-job-id='{job_id}']").first
                try:
                    await card.scroll_into_view_if_needed(timeout=4000)
                    await page.wait_for_timeout(200)
                    title = await read_card_title(card)
                except Exception:
                    title = "Unknown Title"
                ranked.append((title_priority(title, self.priority_skills), title.lower(), job_id))
            ranked.sort()

            for _priority, _title, job_id in ranked:
                if stats["applied"] >= self.max_applications or consecutive_errors >= 3:
                    break
                seen_job_ids.add(job_id)
                card = page.locator(f"li[data-occludable-job-id='{job_id}']").first
                if await card.count() == 0:
                    continue
                try:
                    if await self._consider_job(page, stealth, navigator, card, job_id, stats):
                        matched += 1
                        consecutive_errors = 0
                except Exception as e:
                    logger.exception(f"Unexpected error on job {job_id}: {e}")
                    stats["failed"] += 1
                    consecutive_errors += 1
                    try:
                        await navigator.discard_modal()
                    except Exception:
                        pass

            if stats["applied"] >= self.max_applications or consecutive_errors >= 3:
                break
            off_stack = stats.get("skipped_off_stack", 0)
            if off_stack:
                logger.info(
                    f"Skipped {off_stack} jobs outside JavaScript, React.js, Express.js, Node.js, and MongoDB."
                )
                stats["skipped_off_stack"] = 0
            logger.info("Loading more search results...")
            if not await load_more_results(page, click=stealth.click_element):
                logger.info("No further jobs available in this search.")
                break
        return matched

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
        seen_job_ids = set()

        try:
            if await is_auth_wall(page):
                logger.error(
                    "LinkedIn is logged out or behind an auth wall. "
                    "Sign in inside the Chrome window opened with remote debugging, then run again."
                )
                return stats

            primary_keywords = " OR ".join(
                f"{skill} Developer" for skill in ["JavaScript", "React.js", "Express.js", "Node.js", "MongoDB", "MERN"]
            )
            self.active_title_terms = ["javascript", "react", "express", "node", "mongo", "mern"]
            logger.info("Searching primary skills first: JavaScript, React.js, Express.js, Node.js, MongoDB.")
            await page.goto(self._search_url(primary_keywords), wait_until="domcontentloaded")
            await stealth.random_delay(3.0, 5.0)
            primary_matches = await self._drain_search(page, stealth, navigator, seen_job_ids, stats)

            if stats["applied"] < self.max_applications and primary_matches == 0 and self.secondary_skills:
                secondary_keywords = " OR ".join(f"{skill} Developer" for skill in self.secondary_skills)
                self.active_title_terms = [skill.lower() for skill in self.secondary_skills]
                logger.info(
                    "No primary-skill jobs matched. Searching secondary skills from the profile: "
                    + ", ".join(self.secondary_skills)
                )
                await page.goto(self._search_url(secondary_keywords), wait_until="domcontentloaded")
                await stealth.random_delay(3.0, 5.0)
                await self._drain_search(page, stealth, navigator, seen_job_ids, stats)

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
