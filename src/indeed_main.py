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
from src.indeed_page import (
    INDEED_CARDS,
    card_is_indeed_apply,
    card_should_skip,
    dismiss_indeed_cookies,
    find_indeed_apply_link,
    find_indeed_continue_button,
    has_indeed_company_site,
    indeed_already_applied,
    indeed_apply_blocked,
    indeed_apply_succeeded,
    indeed_card_is_visible,
    indeed_experience_is_fresher,
    indeed_job_key,
    indeed_posted_recently,
    indeed_search_url,
    indeed_view_url,
    load_more_indeed_results,
    read_indeed_card_text,
    read_indeed_company,
    read_indeed_job_id,
    read_indeed_posted_text,
    read_indeed_title,
    wait_for_indeed_apply_step,
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
logger = logging.getLogger("IndeedAutoApplier")

QUESTION_ROOT = ".ia-BasePage-component"
PRIMARY_TERMS = ["javascript", "react", "express", "node", "mongo", "mern"]
PRIMARY_SKILLS = ["JavaScript", "React.js", "Express.js", "Node.js", "MongoDB", "MERN"]


class IndeedRunner:
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
            if "indeed.com" in (candidate.url or ""):
                return candidate
        return await context.new_page()

    async def _login_required(self, page: Page) -> bool:
        url = (page.url or "").lower()
        return "/account/login" in url or "secure.indeed.com/auth" in url

    async def _is_resume_step(self, page: Page) -> bool:
        if "resume-selection" in (page.url or ""):
            return True
        return await page.locator("input[type='radio'][name='resume-selection']").count() > 0

    async def _keep_selected_resume(self, page: Page) -> None:
        radios = page.locator("input[type='radio'][name='resume-selection']")
        count = await radios.count()
        for i in range(count):
            if await radios.nth(i).is_checked():
                return
        if count:
            await radios.first.check()

    async def _click_continue(self, page: Page, stealth) -> bool:
        button = await find_indeed_continue_button(page)
        if button is None:
            return False
        label = " ".join(((await button.inner_text()) or "").lower().split())
        if label in {"save and close", "report an issue"}:
            return False
        before = page.url
        await stealth.click_element(button)
        try:
            await page.wait_for_function(
                """(previous) => {
                    const text = document.body ? document.body.innerText : "";
                    return location.href !== previous
                        || /application has been submitted|your application was submitted|application submitted/i.test(text);
                }""",
                arg=before,
                timeout=4000,
            )
        except Exception:
            try:
                await button.click(timeout=4000)
            except Exception:
                return False
        await stealth.random_delay(0.6, 1.2)
        return True

    async def _fill_apply_form(self, page, stealth, navigator) -> tuple:
        navigator.page = page
        navigator.modal_selector = QUESTION_ROOT
        if not await wait_for_indeed_apply_step(page):
            if await indeed_apply_blocked(page):
                return NavigationResult.FAILED, "Indeed cannot process this application"
            return NavigationResult.FAILED, "Indeed Apply form did not load"
        for _ in range(self.settings.get("max_steps_per_modal", 8)):
            if await indeed_apply_succeeded(page):
                return NavigationResult.SUBMITTED, None
            if await indeed_apply_blocked(page):
                return NavigationResult.FAILED, "Indeed cannot process this application"
            if await self._login_required(page):
                return NavigationResult.FAILED, "Indeed login is required"
            if await self._is_resume_step(page):
                await self._keep_selected_resume(page)
            else:
                ok, reason = await navigator.fill_current_step()
                if not ok:
                    return NavigationResult.DISCARDED, reason
            if not await self._click_continue(page, stealth):
                break
            await wait_for_indeed_apply_step(page, timeout_ms=12000)
        if await indeed_apply_succeeded(page) or await indeed_already_applied(page):
            return NavigationResult.SUBMITTED, None
        if await indeed_apply_blocked(page):
            return NavigationResult.FAILED, "Indeed cannot process this application"
        return NavigationResult.FAILED, "Indeed did not confirm the application"

    async def _open_apply(self, detail: Page, stealth) -> tuple:
        link = await find_indeed_apply_link(detail)
        if link is None:
            if await indeed_already_applied(detail):
                return None, NavigationResult.SUBMITTED, "Already applied on Indeed"
            if await has_indeed_company_site(detail):
                return None, NavigationResult.FAILED, "Apply on company site only"
            return None, NavigationResult.FAILED, "No Indeed Apply button"
        href = (await link.get_attribute("href") or "").strip()
        if href.startswith("/"):
            href = "https://in.indeed.com" + href
        if href.startswith("http"):
            apply_page = await detail.context.new_page()
            try:
                await apply_page.bring_to_front()
            except Exception:
                pass
            try:
                await apply_page.goto(href, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                logger.info("Indeed Apply was slow to load; checking the form anyway.")
            await stealth.random_delay(0.6, 1.2)
            return apply_page, None, None
        await stealth.click_element(link)
        await stealth.random_delay(0.8, 1.4)
        return detail, None, None

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
        title = await read_indeed_title(card)
        company = await read_indeed_company(card)
        card_text = await read_indeed_card_text(card)
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
        logger.info(f"Evaluating '{title}' at '{company}' (ID: {job_id})")
        if indeed_experience_is_fresher(card_text) is False:
            logger.info(f"Skipping '{title}' (experience is above fresher).")
            stats["skipped"] += 1
            return True
        if indeed_posted_recently(await read_indeed_posted_text(card), self.max_post_age_hours) is False:
            logger.info(f"Skipping '{title}' (older than {self.max_post_age_hours} hours).")
            stats["skipped"] += 1
            return True
        if not card_is_indeed_apply(card_text):
            logger.info(f"Skipping '{title}' (Apply on company site only).")
            stats["skipped"] += 1
            return True

        stored_id = indeed_job_key(job_id)
        if self.db.is_already_applied(stored_id):
            logger.info(f"Skipping {stored_id} (already applied).")
            stats["skipped"] += 1
            return True

        detail = await page.context.new_page()
        apply_page = None
        try:
            try:
                await detail.goto(indeed_view_url(job_id), wait_until="domcontentloaded", timeout=30000)
            except Exception:
                logger.info("Indeed job page was slow to load; looking for Apply anyway.")
            await stealth.random_delay(0.6, 1.2)
            if await indeed_already_applied(detail):
                self.db.record_application(stored_id, title, company, "Indeed", "APPLIED")
                stats["skipped"] += 1
                return True
            apply_page, early, early_reason = await self._open_apply(detail, stealth)
            if early is not None:
                reason = early_reason
                result = early
            else:
                self.solver.current_job_title = title
                navigator.page = apply_page
                stealth.page = apply_page
                result, reason = await self._fill_apply_form(apply_page, stealth, navigator)
        finally:
            navigator.page = page
            stealth.page = page
            await self._close_page(page, apply_page)
            await self._close_page(page, detail)

        if reason in {
            "No Indeed Apply button",
            "Apply on company site only",
            "Indeed cannot process this application",
            "Indeed Apply form did not load",
        }:
            logger.info(f"Skipping '{title}' ({reason}).")
            stats["skipped"] += 1
            return True
        if result == NavigationResult.SUBMITTED:
            logger.info(f"Successfully applied on Indeed to {title} ({stored_id}).")
            self.db.record_application(stored_id, title, company, "Indeed", "APPLIED")
            stats["applied"] += 1
        elif result == NavigationResult.DISCARDED:
            logger.warning(f"Discarded Indeed application for {stored_id}: {reason}")
            self.db.record_application(stored_id, title, company, "Indeed", "DISCARDED", reason)
            stats["discarded"] += 1
        else:
            logger.error(f"Failed Indeed application for {stored_id}: {reason}")
            self.db.record_application(stored_id, title, company, "Indeed", "FAILED", reason)
            stats["failed"] += 1

        delay = random.uniform(
            float(self.settings.get("inter_job_delay_min_seconds", 10)),
            float(self.settings.get("inter_job_delay_max_seconds", 20)),
        )
        logger.info(f"Waiting {delay:.1f}s before the next Indeed job...")
        await asyncio.sleep(delay)
        return True

    async def _drain_search(self, page, stealth, navigator, seen_job_ids, stats) -> int:
        matched = 0
        consecutive_errors = 0
        page_index = 0
        while stats["applied"] < self.max_applications and consecutive_errors < 3 and page_index < 3:
            page_index += 1
            cards = page.locator(INDEED_CARDS)
            count = await cards.count()
            logger.info(f"Found {count} Indeed job cards.")
            if count == 0:
                break
            job_ids = []
            for i in range(count):
                card = cards.nth(i)
                if not await indeed_card_is_visible(card):
                    continue
                job_id = await read_indeed_job_id(card)
                if job_id and job_id not in seen_job_ids and job_id not in job_ids:
                    job_ids.append(job_id)
            ranked = []
            for job_id in job_ids:
                card = page.locator(f"{INDEED_CARDS}:has(a[data-jk='{job_id}'])").first
                try:
                    await card.scroll_into_view_if_needed(timeout=4000)
                    title = await read_indeed_title(card)
                except Exception:
                    title = "Unknown Title"
                ranked.append((title_priority(title, self.priority_skills), title.lower(), job_id))
            ranked.sort()
            for _priority, _title, job_id in ranked:
                if stats["applied"] >= self.max_applications or consecutive_errors >= 3:
                    break
                seen_job_ids.add(job_id)
                card = page.locator(f"{INDEED_CARDS}:has(a[data-jk='{job_id}'])").first
                if await card.count() == 0:
                    continue
                try:
                    if await self._consider_job(page, stealth, navigator, card, job_id, stats):
                        matched += 1
                        consecutive_errors = 0
                except Exception as exc:
                    logger.exception(f"Unexpected error on Indeed job {job_id}: {exc}")
                    stats["failed"] += 1
                    consecutive_errors += 1
            off_stack = stats.get("skipped_off_stack", 0)
            if off_stack:
                logger.info(f"Skipped {off_stack} Indeed jobs outside the current skill list.")
                stats["skipped_off_stack"] = 0
            if stats["applied"] >= self.max_applications or consecutive_errors >= 3 or page_index >= 3:
                break
            if not await load_more_indeed_results(page):
                logger.info("No further Indeed results in this search.")
                break
        return matched

    async def run(self) -> Dict[str, int]:
        cdp_url = self.settings.get("cdp_url", "http://localhost:9222")
        logger.info(f"Connecting to Chrome at {cdp_url} for Indeed...")
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
                logger.error("Sign in to Indeed in this Chrome window, then run again.")
                return stats
            await dismiss_indeed_cookies(page)
            self.active_title_terms = list(PRIMARY_TERMS)
            logger.info("Searching Indeed for primary skills: JavaScript, React.js, Express.js, Node.js, MongoDB.")
            primary_matches = 0
            for skill in PRIMARY_SKILLS:
                if stats["applied"] >= self.max_applications:
                    break
                logger.info(f"Indeed primary search: {skill} (entry level, past day).")
                await page.goto(indeed_search_url(skill), wait_until="domcontentloaded")
                await dismiss_indeed_cookies(page)
                await stealth.random_delay(2.0, 3.5)
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
                    "No primary Indeed jobs matched. Searching secondary skills: "
                    + ", ".join(self.secondary_skills)
                )
                for skill in self.secondary_skills:
                    if stats["applied"] >= self.max_applications:
                        break
                    await page.goto(indeed_search_url(skill), wait_until="domcontentloaded")
                    await dismiss_indeed_cookies(page)
                    await stealth.random_delay(2.0, 3.5)
                    await self._drain_search(page, stealth, navigator, seen_job_ids, stats)
        finally:
            try:
                await pw.stop()
            except Exception:
                pass
        logger.info(f"Indeed session completed: {stats}")
        return stats


def main():
    asyncio.run(IndeedRunner().run())


if __name__ == "__main__":
    main()
