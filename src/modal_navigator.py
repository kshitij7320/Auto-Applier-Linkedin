import asyncio
import logging
from enum import Enum
from typing import Optional, Tuple
from playwright.async_api import Page, Locator
from src.stealth import StealthController
from src.extractor import extract_form_step, FormStepData, InputType, ScreeningQuestion
from src.solver import ScreeningSolver, QuestionSolution

logger = logging.getLogger("ModalNavigator")

class NavigationResult(str, Enum):
    SUBMITTED = "SUBMITTED"
    DISCARDED = "DISCARDED"
    FAILED = "FAILED"

class ModalNavigator:
    def __init__(
        self,
        page: Page,
        stealth: StealthController,
        solver: ScreeningSolver,
        max_steps: int = 8,
        confidence_threshold: float = 0.85
    ):
        self.page = page
        self.stealth = stealth
        self.solver = solver
        self.max_steps = max_steps
        self.confidence_threshold = confidence_threshold

    async def discard_modal(self) -> None:
        """Dismisses the modal and confirms discard prompt."""
        try:
            close_btn = self.page.locator(
                "button[aria-label*='Dismiss' i], button[aria-label*='Close' i], .jobs-easy-apply-modal button[data-test-modal-close-btn]"
            ).first
            if await close_btn.count() > 0:
                await self.stealth.click_element(close_btn)
                await asyncio.sleep(0.6)

            confirm_btn = self.page.locator(
                "button[data-control-name='discard_application_confirm_btn'], button:has-text('Discard')"
            ).first
            if await confirm_btn.count() > 0:
                await self.stealth.click_element(confirm_btn)
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Error during modal dismissal: {e}")

    async def fill_current_step(self) -> Tuple[bool, Optional[str]]:
        step_data = await extract_form_step(self.page)

        for q in step_data.questions:
            # Preserve existing non-empty text values (e.g. auto-prefilled phone or name)
            if q.current_value and q.current_value.strip() and q.input_type in [InputType.TEXT, InputType.NUMERIC, InputType.TEXTAREA]:
                continue

            solution = self.solver.solve_question(q)
            if self.solver.should_discard(solution, self.confidence_threshold):
                if q.is_required:
                    logger.warning(f"Discard triggered: confidence {solution.confidence:.2f} < {self.confidence_threshold} on required '{q.question_text}'")
                    return False, f"Low confidence ({solution.confidence:.2f}) on required question: {q.question_text}"
                else:
                    logger.info(f"Skipping optional field with low confidence ({solution.confidence:.2f}): '{q.question_text}'")
                    continue

            await self._apply_solution(q, solution)
            await asyncio.sleep(0.2)

        return True, None

    async def _apply_solution(self, q: ScreeningQuestion, solution: QuestionSolution) -> None:
        if q.input_type in [InputType.TEXT, InputType.NUMERIC, InputType.TEXTAREA]:
            elem = self.page.locator(q.selector).first
            if await elem.count() > 0:
                await self.stealth.type_human(solution.target_value, elem)

        elif q.input_type == InputType.RADIO:
            target_val = solution.target_value.strip().lower()
            # Match radio by value attribute or label text
            radio = self.page.locator(
                f"{q.selector} input[type='radio'][value='{solution.target_value}'], "
                f"{q.selector} label:has-text('{solution.target_value}') input[type='radio']"
            ).first
            if await radio.count() > 0:
                await self.stealth.click_element(radio)
            else:
                # Fallback to case-insensitive text match on radio labels
                all_radios = self.page.locator(f"{q.selector} input[type='radio']")
                rcnt = await all_radios.count()
                for r_idx in range(rcnt):
                    r = all_radios.nth(r_idx)
                    r_val = (await r.get_attribute("value") or "").lower()
                    if r_val == target_val:
                        await self.stealth.click_element(r)
                        break

        elif q.input_type == InputType.SELECT:
            sel = self.page.locator(q.selector).first
            if await sel.count() > 0:
                matched_val = solution.target_value
                for opt in q.options:
                    if opt.label.strip().lower() == solution.target_value.strip().lower() or opt.value.strip().lower() == solution.target_value.strip().lower():
                        matched_val = opt.value
                        break
                await sel.select_option(value=matched_val)

        elif q.input_type == InputType.CHECKBOX:
            cb = self.page.locator(q.selector).first
            if await cb.count() > 0:
                if solution.target_value.lower() in ["true", "yes", "1"]:
                    if not await cb.is_checked():
                        await self.stealth.click_element(cb)

    async def has_validation_errors(self) -> bool:
        errs = self.page.locator(".artdeco-inline-feedback--error, [data-test-form-element-error-messages]")
        return await errs.count() > 0

    async def process_easy_apply_modal(self) -> Tuple[NavigationResult, Optional[str]]:
        for step in range(self.max_steps):
            await self.stealth.random_delay(1.0, 2.0)
            step_data = await extract_form_step(self.page)

            # Check if application already finished/submitted
            if await self.page.locator("h2:has-text('Application submitted')").count() > 0:
                await self.discard_modal()
                return NavigationResult.SUBMITTED, None

            # Fill screening questions on current view
            ok, reason = await self.fill_current_step()
            if not ok:
                await self.discard_modal()
                return NavigationResult.DISCARDED, reason

            # Transition step
            if step_data.has_submit_button:
                submit_btn = self.page.locator("button:has-text('Submit application'), button[aria-label*='Submit application' i]").first
                await self.stealth.click_element(submit_btn)
                await asyncio.sleep(2.0)
                await self.discard_modal()
                return NavigationResult.SUBMITTED, None

            elif step_data.has_review_button:
                rev_btn = self.page.locator("button:has-text('Review'), button[aria-label*='review' i]").first
                await self.stealth.click_element(rev_btn)
                await asyncio.sleep(1.0)

            elif step_data.has_next_button:
                next_btn = self.page.locator("button:has-text('Next'), button[aria-label*='next' i]").first
                await self.stealth.click_element(next_btn)
                await asyncio.sleep(1.0)

                # Check if submission is blocked by validation errors
                if await self.has_validation_errors():
                    await self.discard_modal()
                    return NavigationResult.DISCARDED, "Encountered unresolvable form validation error"

            else:
                await self.discard_modal()
                return NavigationResult.FAILED, "No navigation action button detected in modal"

        await self.discard_modal()
        return NavigationResult.FAILED, f"Exceeded maximum allowable steps ({self.max_steps})"
