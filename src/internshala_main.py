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
from src.internshala_page import (
    INTERNSHALA_CARDS,
    card_is_easy_apply,
    find_internshala_apply_button,
    internshala_already_applied,
    internshala_apply_succeeded,
    internshala_card_is_visible,
    internshala_job_key,
    internshala_posted_recently,
    internshala_search_urls,
    internship_experience_is_fresher,
    internship_should_skip,
    load_more_internshala_results,
    read_internshala_card_text,
    read_internshala_company,
    read_internshala_job_id,
    read_internshala_posted_text,
    read_internshala_skills,
    read_internshala_title,
)
from src.job_page import title_priority
from src.modal_navigator import ModalNavigator, NavigationResult
from src.naukri_page import is_walk_in
from src.solver import ScreeningSolver
from src.stealth import StealthController, connect_to_cdp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("InternshalaAutoApplier")

QUESTION_ROOT = "#easy_apply_modal:visible, #questions:visible, #easy_apply_modal"
PRIMARY_TERMS = ["javascript", "react", "express", "node", "mongo", "mern"]
PRIMARY_SKILLS = ["JavaScript", "React.js", "Express.js", "Node.js", "MongoDB", "MERN"]


class InternshalaRunner:
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
        self.priority_skills = experience.get("primary_skills", PRIMARY_SKILLS[:-1])
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
            if "internshala.com" in (candidate.url or ""):
                return candidate
        return await context.new_page()

    async def _login_required(self, page: Page) -> bool:
        url = (page.url or "").lower()
        return "/login" in url or "/registration" in url

    async def _select_immediate_availability(self, page: Page, stealth) -> None:
        label = page.locator("#easy_apply_modal label", has_text="available to join immediately").first
        try:
            if await label.count() > 0 and await label.is_visible():
                await stealth.click_element(label)
        except Exception:
            return

    async def _wait_for_submission(self, page: Page) -> bool:
        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body ? document.body.innerText : "";
                    if (/application submitted/i.test(text)) return true;
                    const button = document.querySelector("#easy_apply_button, #top_easy_apply_button");
                    const label = button ? (button.innerText || "").replace(/\\s+/g, " ").trim().toLowerCase() : "";
                    return label === "already applied";
                }""",
                timeout=8000,
            )
            return True
        except Exception:
            return False

    async def _submit_modal(self, page: Page, stealth) -> bool:
        button = page.locator("#easy_apply_modal #submit, #questions #submit, #questions button:has-text('Submit')").first
        try:
            if await button.count() == 0 or not await button.is_visible():
                return False
        except Exception:
            return False
        await stealth.click_element(button)
        await stealth.random_delay(0.8, 1.4)
        return True

    async def _active_question_root(self, page: Page) -> str:
        questions = page.locator("#questions")
        try:
            if await questions.count() > 0 and await questions.is_visible():
                return "#questions"
        except Exception:
            pass
        return "#easy_apply_modal"

    async def _fill_apply_form(self, page, stealth, navigator) -> tuple:
        navigator.page = page
        navigator.modal_selector = await self._active_question_root(page)
        try:
            await page.wait_for_selector("#easy_apply_modal", state="visible", timeout=8000)
        except Exception:
            return NavigationResult.FAILED, "Internshala Apply form did not open"
        for _ in range(self.settings.get("max_steps_per_modal", 8)):
            if await internshala_apply_succeeded(page) or await internshala_already_applied(page):
                return NavigationResult.SUBMITTED, None
            navigator.modal_selector = await self._active_question_root(page)
            ok, reason = await navigator.fill_current_step()
            if not ok:
                return NavigationResult.DISCARDED, reason
            await self._select_immediate_availability(page, stealth)
            if not await self._submit_modal(page, stealth):
                break
            if await self._wait_for_submission(page):
                return NavigationResult.SUBMITTED, None
            await stealth.random_delay(0.4, 0.8)
        if await internshala_apply_succeeded(page) or await internshala_already_applied(page):
            return NavigationResult.SUBMITTED, None
        return NavigationResult.FAILED, "Internshala did not confirm the application"

    async def _close_page(self, search_page: Page, extra: Page) -> None:
        if extra is not None and extra is not search_page:
            try:
                await extra.close()
            except Exception:
                pass

    async def _consider_job(self, page, stealth, navigator, card, job_id: str, stats: Dict[str, int]) -> bool:
        try:
            await card.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        title = await read_internshala_title(card)
        company = await read_internshala_company(card)
        skills = await read_internshala_skills(card)
        on_stack = internship_should_skip(
            title, skills, self.active_title_terms, self.excluded_stacks, self.excluded_titles
        ) is None
        if is_walk_in(title):
            logger.info(f"Skipping '{title}' (walk-in drive, not an online application).")
            stats["skipped"] += 1
            return on_stack
        if not on_stack:
            stats["skipped"] += 1
            stats["skipped_off_stack"] = stats.get("skipped_off_stack", 0) + 1
            return False
        logger.info(f"Evaluating '{title}' at '{company}' (ID: {job_id})")
        card_text = await read_internshala_card_text(card)
        if internship_experience_is_fresher(card_text) is False:
            logger.info(f"Skipping '{title}' (experience is above fresher).")
            stats["skipped"] += 1
            return True
        if internshala_posted_recently(await read_internshala_posted_text(card), self.max_post_age_hours) is False:
            logger.info(f"Skipping '{title}' (older than {self.max_post_age_hours} hours).")
            stats["skipped"] += 1
            return True
        class_name = await card.get_attribute("class") or ""
        if not card_is_easy_apply(class_name):
            logger.info(f"Skipping '{title}' (not an Internshala Apply button).")
            stats["skipped"] += 1
            return True

        stored_id = internshala_job_key(job_id)
        if self.db.is_already_applied(stored_id):
            logger.info(f"Skipping {stored_id} (already applied).")
            stats["skipped"] += 1
            return True

        link = card.locator("a.job-title-href").first
        href = (await link.get_attribute("href") or "").strip() if await link.count() else ""
        if href.startswith("/"):
            href = "https://internshala.com" + href
        if not href.startswith("http"):
            logger.info(f"Skipping '{title}' (Internshala listing link is missing).")
            stats["skipped"] += 1
            return True
        detail = await page.context.new_page()
        try:
            if href.startswith("http"):
                try:
                    await detail.goto(href, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    logger.info("Internshala listing was slow to load; looking for Apply anyway.")
            await stealth.random_delay(0.6, 1.2)
            if await internshala_already_applied(detail):
                self.db.record_application(stored_id, title, company, "Internshala", "APPLIED")
                stats["skipped"] += 1
                return True
            button = await find_internshala_apply_button(detail)
            if button is None:
                logger.info(f"Skipping '{title}' (Internshala has no Apply now button).")
                stats["skipped"] += 1
                return True
            self.solver.current_job_title = title
            navigator.page = detail
            stealth.page = detail
            await stealth.click_element(button)
            result, reason = await self._fill_apply_form(detail, stealth, navigator)
        finally:
            navigator.page = page
            stealth.page = page
            await self._close_page(page, detail)

        if result == NavigationResult.SUBMITTED:
            logger.info(f"Successfully applied on Internshala to {title} ({stored_id}).")
            self.db.record_application(stored_id, title, company, "Internshala", "APPLIED")
            stats["applied"] += 1
        elif result == NavigationResult.DISCARDED:
            logger.warning(f"Discarded Internshala application for {stored_id}: {reason}")
            self.db.record_application(stored_id, title, company, "Internshala", "DISCARDED", reason)
            stats["discarded"] += 1
        else:
            logger.error(f"Failed Internshala application for {stored_id}: {reason}")
            self.db.record_application(stored_id, title, company, "Internshala", "FAILED", reason)
            stats["failed"] += 1

        delay = random.uniform(
            float(self.settings.get("inter_job_delay_min_seconds", 10)),
            float(self.settings.get("inter_job_delay_max_seconds", 20)),
        )
        logger.info(f"Waiting {delay:.1f}s before the next Internshala listing...")
        await asyncio.sleep(delay)
        return True

    async def _drain_search(self, page, stealth, navigator, seen_job_ids, stats) -> int:
        matched = 0
        consecutive_errors = 0
        page_index = 0
        while stats["applied"] < self.max_applications and consecutive_errors < 3 and page_index < 3:
            page_index += 1
            cards = page.locator(INTERNSHALA_CARDS)
            count = await cards.count()
            logger.info(f"Found {count} Internshala listings.")
            if count == 0:
                break
            job_ids = []
            for i in range(count):
                card = cards.nth(i)
                if not await internshala_card_is_visible(card):
                    continue
                job_id = await read_internshala_job_id(card)
                if job_id and job_id not in seen_job_ids and job_id not in job_ids:
                    job_ids.append(job_id)
            ranked = []
            for job_id in job_ids:
                card = page.locator(f"#individual_internship_{job_id}").first
                try:
                    await card.scroll_into_view_if_needed(timeout=4000)
                    title = await read_internshala_title(card)
                except Exception:
                    title = "Unknown Title"
                ranked.append((title_priority(title, self.priority_skills), title.lower(), job_id))
            ranked.sort()
            for _priority, _title, job_id in ranked:
                if stats["applied"] >= self.max_applications or consecutive_errors >= 3:
                    break
                seen_job_ids.add(job_id)
                card = page.locator(f"#individual_internship_{job_id}").first
                if await card.count() == 0:
                    continue
                try:
                    if await self._consider_job(page, stealth, navigator, card, job_id, stats):
                        matched += 1
                        consecutive_errors = 0
                except Exception as exc:
                    logger.exception(f"Unexpected error on Internshala listing {job_id}: {exc}")
                    stats["failed"] += 1
                    consecutive_errors += 1
            off_stack = stats.get("skipped_off_stack", 0)
            if off_stack:
                logger.info(f"Skipped {off_stack} Internshala listings outside the current skill list.")
                stats["skipped_off_stack"] = 0
            if stats["applied"] >= self.max_applications or consecutive_errors >= 3 or page_index >= 3:
                break
            if not await load_more_internshala_results(page):
                logger.info("No further Internshala results in this search.")
                break
        return matched

    async def run(self) -> Dict[str, int]:
        cdp_url = self.settings.get("cdp_url", "http://localhost:9222")
        logger.info(f"Connecting to Chrome at {cdp_url} for Internshala...")
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
                logger.error("Sign in to Internshala in this Chrome window, then run again.")
                return stats
            self.active_title_terms = list(PRIMARY_TERMS)
            logger.info("Searching Internshala for primary skills: JavaScript, React.js, Express.js, Node.js, MongoDB.")
            primary_matches = 0
            for skill in PRIMARY_SKILLS:
                if stats["applied"] >= self.max_applications:
                    break
                for url in internshala_search_urls(skill):
                    if stats["applied"] >= self.max_applications:
                        break
                    kind = "internships" if "/internships/" in url else "fresher jobs"
                    logger.info(f"Internshala primary search: {skill} ({kind}, past day).")
                    await page.goto(url, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_selector(INTERNSHALA_CARDS, timeout=12000)
                    except Exception:
                        logger.info(f"No Internshala {kind} for {skill}.")
                    await stealth.random_delay(1.5, 2.5)
                    primary_matches += await self._drain_search(page, stealth, navigator, seen_job_ids, stats)
            if primary_matches:
                label = "title was" if primary_matches == 1 else "titles were"
                logger.info(
                    f"{primary_matches} primary-skill {label} in these results. "
                    "Not searching secondary skills."
                )
            elif stats["applied"] < self.max_applications and self.secondary_skills:
                self.active_title_terms = [skill.lower() for skill in self.secondary_skills]
                logger.info(
                    "No primary Internshala listings matched. Searching secondary skills: "
                    + ", ".join(self.secondary_skills)
                )
                for skill in self.secondary_skills:
                    if stats["applied"] >= self.max_applications:
                        break
                    for url in internshala_search_urls(skill):
                        if stats["applied"] >= self.max_applications:
                            break
                        await page.goto(url, wait_until="domcontentloaded")
                        await stealth.random_delay(1.5, 2.5)
                        await self._drain_search(page, stealth, navigator, seen_job_ids, stats)
        finally:
            try:
                await pw.stop()
            except Exception:
                pass
        logger.info(f"Internshala session completed: {stats}")
        return stats


def main():
    asyncio.run(InternshalaRunner().run())


if __name__ == "__main__":
    main()
