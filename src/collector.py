import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, List, Set
from playwright.async_api import Page, Locator

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.job_page import (
    find_easy_apply_button,
    is_auth_wall,
    job_title_link,
    load_more_results,
    posted_within_hours,
    read_card_company,
    read_card_posted_text,
    read_card_title,
    read_job_id,
    title_rejection_reason,
    wait_for_easy_apply_modal,
    wait_for_selected_job,
    open_job_matches,
)
from src.modal_navigator import ModalNavigator
from src.solver import ScreeningSolver
from src.stealth import connect_to_cdp, StealthController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("QuestionCollector")

class QuestionCollector:
    def __init__(self, config_dir: str = "config", output_path: str = "storage/collected_questions.json"):
        settings_path = os.path.join(config_dir, "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            self.settings: Dict[str, Any] = json.load(f)

        self.output_path = output_path
        self.required_title_terms = self.settings.get("required_title_terms", [])
        self.excluded_stacks = self.settings.get("excluded_stacks", [])
        self.excluded_titles = [t.lower() for t in self.settings.get("excluded_titles", [])]
        self.max_jobs_to_scan = int(self.settings.get("max_questions_scan", 15))
        self.solver = ScreeningSolver(
            profile_path=os.path.join(config_dir, "profile.json"),
            question_bank_path=output_path,
        )
        self.solver.question_bank.seed(self.solver.preview_answer, refresh=True)
        self.solver.question_bank.refresh_saved(self.solver.preview_answer)
        self.collected_questions = self.solver.question_bank.questions

    async def discard_modal(self, page: Page, stealth: StealthController) -> None:
        """Safely dismisses the modal and confirms discard prompt."""
        try:
            close_btn = page.locator(
                "button[aria-label*='Dismiss' i], button[aria-label*='Close' i], .jobs-easy-apply-modal button[data-test-modal-close-btn]"
            ).first
            if await close_btn.count() > 0:
                await stealth.click_element(close_btn)
                await asyncio.sleep(0.6)

            confirm_btn = page.locator(
                "button[data-control-name='discard_application_confirm_btn'], button:has-text('Discard')"
            ).first
            if await confirm_btn.count() > 0:
                await stealth.click_element(confirm_btn)
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Error while dismissing modal: {e}")

    async def collect_from_modal(self, page: Page, stealth: StealthController, job_id: str, job_title: str) -> None:
        """Walks every modal step, saves each question with its prepared answer, and does not submit."""
        self.solver.current_job_title = job_title
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=self.solver,
            max_steps=int(self.settings.get("max_steps_per_modal", 8)),
            confidence_threshold=float(self.settings.get("confidence_threshold", 0.85)),
        )
        captured = await navigator.capture_questions()
        self.collected_questions = self.solver.question_bank.questions
        for item in captured:
            state = "ready" if item["covered"] else "NEEDS ANSWER"
            logger.info(f"  [{state}] {item['question_text']} -> {item['answer'] or '(blank)'}")

    async def run(self) -> Dict[str, Any]:
        cdp_url = self.settings.get("cdp_url", "http://localhost:9222")
        logger.info(f"Connecting to CDP instance at {cdp_url}...")

        pw, browser, page = await connect_to_cdp(cdp_url)
        stealth = StealthController(page)

        scanned_count = 0
        seen_job_ids: Set[str] = set()
        try:
            search_url = self.settings.get("search_url")
            logger.info(f"Navigating to entry-level search: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded")
            await stealth.random_delay(3.0, 5.0)

            if await is_auth_wall(page):
                logger.error("LinkedIn is logged out. Sign in in this Chrome window, then collect questions again.")
                return self.collected_questions

            while scanned_count < self.max_jobs_to_scan:
                job_cards = page.locator("li[data-occludable-job-id]")
                count = await job_cards.count()
                logger.info(f"Found {count} job cards.")
                if count == 0:
                    break

                page_had_new_card = False
                for i in range(count):
                    if scanned_count >= self.max_jobs_to_scan:
                        break

                    card = job_cards.nth(i)
                    job_id = await read_job_id(card)
                    if not job_id or job_id in seen_job_ids:
                        continue
                    seen_job_ids.add(job_id)
                    page_had_new_card = True

                    title = await read_card_title(card)
                    company = await read_card_company(card)

                    if title_rejection_reason(
                        title,
                        required_terms=self.required_title_terms,
                        excluded_stacks=self.excluded_stacks,
                        excluded_seniority=self.excluded_titles,
                    ):
                        logger.info(f"Skipping '{title}' (outside the React/Node/Express/Mongo stack).")
                        continue

                    age_text = await read_card_posted_text(card)
                    if posted_within_hours(age_text, int(self.settings.get("max_post_age_hours", 24))) is False:
                        logger.info(f"Skipping '{title}' (older than 24 hours).")
                        continue

                    logger.info(f"[{scanned_count+1}/{self.max_jobs_to_scan}] Inspecting: '{title}' at '{company}' (ID: {job_id})")
                    if await page.locator(".jobs-easy-apply-modal").count() > 0:
                        await ModalNavigator(page, stealth, self.solver).discard_modal()
                    try:
                        await card.scroll_into_view_if_needed(timeout=4000)
                    except Exception:
                        pass
                    await stealth.click_element(await job_title_link(card))
                    await stealth.random_delay(1.0, 2.0)
                    if not await wait_for_selected_job(page, job_id, title) and not await open_job_matches(page, job_id, title):
                        logger.info(f"  Details pane did not open for {job_id}. Skipping.")
                        continue

                    easy_apply_btn = await find_easy_apply_button(page)
                    if easy_apply_btn is None:
                        logger.info(f"  No Easy Apply for {job_id}. Skipping.")
                        continue

                    await stealth.click_element(easy_apply_btn)
                    if not await wait_for_easy_apply_modal(page):
                        logger.info(f"  Easy Apply modal did not open for {job_id}. Skipping.")
                        continue

                    await self.collect_from_modal(page, stealth, job_id, title)
                    scanned_count += 1
                    await asyncio.sleep(1.5)

                if scanned_count >= self.max_jobs_to_scan:
                    break
                if not page_had_new_card or not await load_more_results(page, click=stealth.click_element):
                    break

        finally:
            try:
                await pw.stop()
            except Exception:
                pass

        missing = self.solver.question_bank.uncovered_required()
        logger.info(f"Question collection completed. {len(self.collected_questions)} unique questions saved to {self.output_path}.")
        if missing:
            logger.warning(f"{len(missing)} required questions still have no answer: {missing}")
        else:
            logger.info("Every saved required question has an answer ready for apply.")
        return self.collected_questions

def main():
    collector = QuestionCollector()
    asyncio.run(collector.run())

if __name__ == "__main__":
    main()
