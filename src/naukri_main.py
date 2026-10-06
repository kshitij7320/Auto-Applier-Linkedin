import asyncio
import json
import logging
import os
import random
import sys
from typing import Dict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from playwright.async_api import Page

from src.db import DatabaseTracker
from src.job_page import title_priority
from src.modal_navigator import ModalNavigator, NavigationResult
from src.naukri_page import (
    card_should_skip,
    experience_is_fresher,
    find_naukri_apply_button,
    has_company_site_apply,
    is_walk_in,
    load_more_naukri_results,
    naukri_already_applied,
    naukri_apply_succeeded,
    naukri_job_key,
    naukri_posted_recently,
    naukri_search_url,
    read_naukri_company,
    read_naukri_experience,
    read_naukri_job_id,
    read_naukri_posted_text,
    read_naukri_title,
)
from src.solver import ScreeningSolver
from src.stealth import StealthController, connect_to_cdp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("NaukriAutoApplier")

QUESTION_ROOT = (
    "#apply-quest-container, .chatbot_Drawer, .chatBotDrawer, "
    ".naukri-apply-questions, [role='dialog']"
)
PRIMARY_TERMS = ["javascript", "react", "express", "node", "mongo", "mern"]
NAUKRI_CARDS = ".srp-jobtuple-wrapper"


class NaukriRunner:
    def __init__(self, config_dir: str = "config", db_path: str = "storage/tracker.db"):
        with open(os.path.join(config_dir, "settings.json"), "r", encoding="utf-8") as handle:
            self.settings = json.load(handle)
        self.profile_path = os.path.join(config_dir, "profile.json")
        self.db = DatabaseTracker(db_path)
        self.db.init_db()
        self.solver = ScreeningSolver(
            profile_path=self.profile_path,
            db_tracker=self.db,
            question_bank_path=os.path.join("storage", "collected_questions.json"),
        )
        experience = self.solver.profile.get("experience", {})
        self.priority_skills = experience.get(
            "primary_skills", ["JavaScript", "React.js", "Express.js", "Node.js", "MongoDB"]
        )
        excluded = {item.lower() for item in self.settings.get("excluded_stacks", [])}
        self.secondary_skills = [
            skill for skill in experience.get("secondary_skills", [])
            if skill.lower() not in excluded
        ]
        self.active_title_terms = list(PRIMARY_TERMS)
        self.excluded_stacks = self.settings.get("excluded_stacks", [])
        self.excluded_titles = [item.lower() for item in self.settings.get("excluded_titles", [])]
        self.max_applications = int(self.settings.get("max_applications_per_session", 20))
        self.max_post_age_hours = int(self.settings.get("max_post_age_hours", 24))

    async def _open_page(self, browser):
        context = browser.contexts[0]
        for candidate in context.pages:
            if "naukri.com" in (candidate.url or ""):
                return candidate
        return await context.new_page()

    async def _login_required(self, page: Page) -> bool:
        url = (page.url or "").lower()
        return "naukri.com/nlogin" in url or "/mnj/login" in url

    async def _complete_apply(self, page, stealth, navigator) -> tuple:
        button = await find_naukri_apply_button(page)
        if button is None:
            if await naukri_already_applied(page):
                return NavigationResult.SUBMITTED, "Already applied on Naukri"
            if await has_company_site_apply(page):
                return NavigationResult.FAILED, "Apply on company site only"
            return NavigationResult.FAILED, "No on-site Apply button"
        logger.info("Clicking Naukri Apply...")
        await stealth.click_element(button)
        await stealth.random_delay(1.0, 1.8)
        if await self._login_required(page):
            return NavigationResult.FAILED, "Naukri login is required"

        for _ in range(self.settings.get("max_steps_per_modal", 8)):
            if await naukri_apply_succeeded(page):
                await self._dismiss_followups(page)
                return NavigationResult.SUBMITTED, None
            root = page.locator(QUESTION_ROOT).first
            if await root.count() == 0 or not await root.is_visible():
                await asyncio.sleep(0.6)
                if await naukri_apply_succeeded(page):
                    await self._dismiss_followups(page)
                    return NavigationResult.SUBMITTED, None
                break
            navigator.modal_selector = QUESTION_ROOT
            ok, reason = await navigator.fill_current_step()
            if not ok:
                return NavigationResult.DISCARDED, reason
            submit = root.locator(
                "button:has-text('Submit'), button:has-text('Save'), button:has-text('Continue'), button:has-text('Next')"
            ).first
            if await submit.count() == 0 or not await submit.is_visible():
                break
            await stealth.click_element(submit)
            await stealth.random_delay(0.8, 1.4)

        if await naukri_apply_succeeded(page) or await naukri_already_applied(page):
            await self._dismiss_followups(page)
            return NavigationResult.SUBMITTED, None
        return NavigationResult.FAILED, "Naukri did not confirm the application"

    async def _open_job(self, page: Page, stealth, card) -> Page:
        """Open the job listing itself. Apply lives on that page, not the search card."""
        link = card.locator("a.title, a[href*='job-listings']").first
        if await link.count() == 0:
            link = card.locator("a").first
        href = ""
        if await link.count() > 0:
            href = (await link.get_attribute("href") or "").strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://www.naukri.com" + href
        if not href.startswith("http"):
            return page
        detail = await page.context.new_page()
        try:
            await detail.bring_to_front()
        except Exception:
            pass
        try:
            await detail.goto(href, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            logger.info("Naukri job page was slow to load; looking for Apply anyway.")
        await stealth.random_delay(0.6, 1.2)
        return detail

    async def _close_detail(self, search_page: Page, detail: Page) -> None:
        if detail is not search_page:
            try:
                await detail.close()
            except Exception:
                pass

    async def _dismiss_followups(self, page: Page) -> None:
        for name in ("Not now", "Skip", "No thanks", "Close"):
            button = page.get_by_role("button", name=name, exact=True)
            try:
                if await button.count() > 0 and await button.first.is_visible():
                    await button.first.click(timeout=3000)
                    return
            except Exception:
                continue

    async def _consider_job(self, page, stealth, navigator, card, job_id: str, stats: Dict[str, int]) -> bool:
        try:
            await card.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        title = await read_naukri_title(card)
        company = await read_naukri_company(card)
        logger.info(f"Evaluating '{title}' at '{company}' (ID: {job_id})")
        on_stack = card_should_skip(
            title, "", self.active_title_terms, self.excluded_stacks, self.excluded_titles
        ) is None
        if is_walk_in(title):
            logger.info(f"Skipping '{title}' (walk-in drive, not an online application).")
            stats["skipped"] += 1
            return on_stack
        if not on_stack:
            stats["skipped"] += 1
            stats["skipped_off_stack"] = stats.get("skipped_off_stack", 0) + 1
            return False
        exp_text = await read_naukri_experience(card)
        if experience_is_fresher(exp_text) is False:
            logger.info(f"Skipping '{title}' (asks for {exp_text or 'more experience'}, above fresher).")
            stats["skipped"] += 1
            return True
        if naukri_posted_recently(await read_naukri_posted_text(card), self.max_post_age_hours) is False:
            logger.info(f"Skipping '{title}' (older than {self.max_post_age_hours} hours).")
            stats["skipped"] += 1
            return True

        stored_id = naukri_job_key(job_id)
        if self.db.is_already_applied(stored_id):
            logger.info(f"Skipping {stored_id} (already applied).")
            stats["skipped"] += 1
            return True

        detail = await self._open_job(page, stealth, card)
        try:
            if await naukri_already_applied(detail):
                self.db.record_application(stored_id, title, company, "Naukri", "APPLIED")
                stats["skipped"] += 1
                return True

            self.solver.current_job_title = title
            navigator.page = detail
            stealth.page = detail
            try:
                await detail.wait_for_selector(
                    "#apply-button, #walkin-button, button.apply-button, [class*='apply-button']",
                    timeout=8000,
                )
            except Exception:
                pass
            result, reason = await self._complete_apply(detail, stealth, navigator)
        finally:
            navigator.page = page
            stealth.page = page
            await self._close_detail(page, detail)
        if reason in {"No on-site Apply button", "Apply on company site only"}:
            if reason == "Apply on company site only":
                logger.info(f"Skipping '{title}' (Apply on company site only).")
            else:
                logger.info(f"Skipping '{title}' (Naukri has no Apply button on this job).")
            stats["skipped"] += 1
            return True
        if result == NavigationResult.SUBMITTED:
            logger.info(f"Successfully applied on Naukri to {title} ({stored_id}).")
            self.db.record_application(stored_id, title, company, "Naukri", "APPLIED")
            stats["applied"] += 1
        elif result == NavigationResult.DISCARDED:
            logger.warning(f"Discarded Naukri application for {stored_id}: {reason}")
            self.db.record_application(stored_id, title, company, "Naukri", "DISCARDED", reason)
            stats["discarded"] += 1
        else:
            logger.error(f"Failed Naukri application for {stored_id}: {reason}")
            self.db.record_application(stored_id, title, company, "Naukri", "FAILED", reason)
            stats["failed"] += 1

        delay = random.uniform(
            float(self.settings.get("inter_job_delay_min_seconds", 10)),
            float(self.settings.get("inter_job_delay_max_seconds", 20)),
        )
        logger.info(f"Waiting {delay:.1f}s before the next Naukri job...")
        await asyncio.sleep(delay)
        return True

    async def _drain_search(self, page, stealth, navigator, seen_job_ids, stats) -> int:
        matched = 0
        consecutive_errors = 0
        page_index = 0
        while stats["applied"] < self.max_applications and consecutive_errors < 3 and page_index < 3:
            page_index += 1
            cards = page.locator(NAUKRI_CARDS)
            count = await cards.count()
            logger.info(f"Found {count} Naukri job cards.")
            if count == 0:
                break
            job_ids = []
            for i in range(count):
                job_id = await read_naukri_job_id(cards.nth(i))
                if job_id and job_id not in seen_job_ids and job_id not in job_ids:
                    job_ids.append(job_id)
            ranked = []
            for job_id in job_ids:
                card = page.locator(f"[data-job-id='{job_id}']").first
                try:
                    await card.scroll_into_view_if_needed(timeout=4000)
                    title = await read_naukri_title(card)
                except Exception:
                    title = "Unknown Title"
                ranked.append((title_priority(title, self.priority_skills), title.lower(), job_id))
            ranked.sort()
            for _priority, _title, job_id in ranked:
                if stats["applied"] >= self.max_applications or consecutive_errors >= 3:
                    break
                seen_job_ids.add(job_id)
                card = page.locator(f"[data-job-id='{job_id}']").first
                if await card.count() == 0:
                    continue
                try:
                    if await self._consider_job(page, stealth, navigator, card, job_id, stats):
                        matched += 1
                        consecutive_errors = 0
                except Exception as exc:
                    logger.exception(f"Unexpected error on Naukri job {job_id}: {exc}")
                    stats["failed"] += 1
                    consecutive_errors += 1
            off_stack = stats.get("skipped_off_stack", 0)
            if off_stack:
                logger.info(f"Skipped {off_stack} Naukri jobs outside the current skill list.")
                stats["skipped_off_stack"] = 0
            if stats["applied"] >= self.max_applications or consecutive_errors >= 3 or page_index >= 3:
                break
            if not await load_more_naukri_results(page):
                logger.info("No further Naukri results in this search.")
                break
        return matched

    async def run(self) -> Dict[str, int]:
        cdp_url = self.settings.get("cdp_url", "http://localhost:9222")
        logger.info(f"Connecting to Chrome at {cdp_url} for Naukri...")
        pw, browser, _page = await connect_to_cdp(cdp_url)
        page = await self._open_page(browser)
        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=self.solver,
            max_steps=self.settings.get("max_steps_per_modal", 8),
            confidence_threshold=self.settings.get("confidence_threshold", 0.85),
            modal_selector=QUESTION_ROOT,
        )
        stats = {"applied": 0, "discarded": 0, "failed": 0, "skipped": 0}
        seen_job_ids = set()
        try:
            if await self._login_required(page):
                logger.error("Sign in to Naukri in this Chrome window, then run again.")
                return stats
            self.active_title_terms = list(PRIMARY_TERMS)
            logger.info("Searching Naukri for primary skills: JavaScript, React.js, Express.js, Node.js, MongoDB.")
            primary_matches = 0
            for skill in ["JavaScript", "React.js", "Express.js", "Node.js", "MongoDB", "MERN"]:
                if stats["applied"] >= self.max_applications:
                    break
                logger.info(f"Naukri primary search: {skill} (0-1 years, past day).")
                await page.goto(naukri_search_url(skill), wait_until="domcontentloaded")
                await stealth.random_delay(2.0, 3.5)
                primary_matches += await self._drain_search(page, stealth, navigator, seen_job_ids, stats)
            if primary_matches:
                logger.info(
                    f"{primary_matches} primary-skill titles were in these results. "
                    "Not searching secondary skills."
                )
            elif stats["applied"] < self.max_applications and self.secondary_skills:
                self.active_title_terms = [skill.lower() for skill in self.secondary_skills]
                logger.info("No primary Naukri jobs matched. Searching secondary skills: " + ", ".join(self.secondary_skills))
                for skill in self.secondary_skills:
                    if stats["applied"] >= self.max_applications:
                        break
                    await page.goto(naukri_search_url(skill), wait_until="domcontentloaded")
                    await stealth.random_delay(2.0, 3.5)
                    await self._drain_search(page, stealth, navigator, seen_job_ids, stats)
        finally:
            try:
                await pw.stop()
            except Exception:
                pass
        logger.info(f"Naukri session completed: {stats}")
        return stats


def main():
    asyncio.run(NaukriRunner().run())


if __name__ == "__main__":
    main()
