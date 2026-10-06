import asyncio
import logging
from enum import Enum
from typing import List, Optional, Tuple

from playwright.async_api import Locator, Page

from src.extractor import (
    CONTINUE_APPLYING_BUTTON,
    NEXT_BUTTON,
    REVIEW_BUTTON,
    SUBMIT_BUTTON,
    FormStepData,
    InputType,
    ScreeningQuestion,
    extract_form_step,
    resolve_modal,
)
from src.solver import QuestionSolution, ScreeningSolver
from src.stealth import StealthController

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
        confidence_threshold: float = 0.85,
        modal_selector: str = ".jobs-easy-apply-modal",
    ):
        self.page = page
        self.stealth = stealth
        self.solver = solver
        self.max_steps = max_steps
        self.confidence_threshold = confidence_threshold
        self.modal_selector = modal_selector

    def _save_prompt(self) -> Locator:
        return self.page.locator("[role='dialog'], [role='alertdialog']").filter(
            has_text="Save this application?"
        )

    async def save_prompt_open(self) -> bool:
        prompt = self._save_prompt()
        try:
            return await prompt.count() > 0 and await prompt.last.is_visible()
        except Exception:
            return False

    async def handle_save_prompt(self, choice: str) -> bool:
        """Clear LinkedIn's Save/Discard prompt. choice is 'discard' or 'return'."""
        if not await self.save_prompt_open():
            return False
        prompt = self._save_prompt().last
        if choice == "discard":
            button = prompt.get_by_role("button", name="Discard", exact=True)
        else:
            button = prompt.locator(
                "button[aria-label*='Dismiss' i], button[aria-label*='Close' i]"
            ).first
        try:
            if await button.count() == 0 or not await button.is_visible():
                return False
            await button.click(timeout=4000)
            await asyncio.sleep(0.4)
            return True
        except Exception as e:
            logger.warning(f"Could not handle the save prompt ({choice}): {e}")
            return False

    async def discard_modal(self) -> None:
        """Dismisses the Easy Apply modal and confirms discard so the next job can open."""
        try:
            for _ in range(4):
                if await self.save_prompt_open():
                    await self.handle_save_prompt("discard")
                    await asyncio.sleep(0.3)
                modal = self.page.locator(".jobs-easy-apply-modal").first
                if await modal.count() == 0 or not await modal.is_visible():
                    return
                close_btn = modal.locator(
                    "button[aria-label*='Dismiss' i], button[aria-label*='Close' i], button[data-test-modal-close-btn]"
                ).first
                if await close_btn.count() > 0 and await close_btn.is_visible():
                    await self._safe_click(close_btn)
                else:
                    await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.45)
                if await self.save_prompt_open():
                    await self.handle_save_prompt("discard")
        except Exception as e:
            logger.warning(f"Error during modal dismissal: {e}")

    async def fill_current_step(self) -> Tuple[bool, Optional[str]]:
        step_data = await extract_form_step(self.page, self.modal_selector)

        for q in step_data.questions:
            if (
                q.current_value
                and q.current_value.strip()
                and q.input_type in [InputType.TEXT, InputType.NUMERIC, InputType.TEXTAREA]
            ):
                continue

            solution = self.solver.solve_question(q)
            if self.solver.should_discard(solution, self.confidence_threshold):
                if q.is_required:
                    logger.warning(
                        f"Discard triggered: confidence {solution.confidence:.2f} < "
                        f"{self.confidence_threshold} on required '{q.question_text}'"
                    )
                    return False, (
                        f"Low confidence ({solution.confidence:.2f}) on required question: {q.question_text}"
                    )
                logger.info(
                    f"Skipping optional field with low confidence ({solution.confidence:.2f}): '{q.question_text}'"
                )
                continue

            if q.input_type == InputType.CHECKBOX and solution.target_value.lower() not in {"true", "yes", "1"}:
                await self._set_checkbox(q, "false")
                continue

            applied = await self._apply_solution(q, solution)
            if not applied and q.is_required:
                return False, f"Could not fill required question: {q.question_text}"
            await asyncio.sleep(0.15)

        return True, None

    async def _apply_solution(self, q: ScreeningQuestion, solution: QuestionSolution) -> bool:
        try:
            if q.input_type in [InputType.TEXT, InputType.NUMERIC, InputType.TEXTAREA]:
                return await self._fill_text(q, solution.target_value)
            if q.input_type == InputType.RADIO:
                return await self._select_radio(q, solution.target_value)
            if q.input_type == InputType.SELECT:
                return await self._select_option(q, solution.target_value)
            if q.input_type == InputType.CHECKBOX:
                return await self._set_checkbox(q, solution.target_value)
        except Exception as e:
            logger.warning(f"Failed to apply answer for '{q.question_text}': {e}")
        return False

    async def _fill_text(self, q: ScreeningQuestion, value: str) -> bool:
        if not value:
            return False
        elem = self.page.locator(q.selector).first
        if await elem.count() == 0:
            return False
        await self._safe_click(elem)
        await elem.fill(value)
        await elem.evaluate("""el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        question = q.question_text.lower()
        location_field = any(token in question for token in ["city", "location", "address"])
        if q.is_combobox or location_field or await self._is_typeahead(elem):
            await self._choose_typeahead_option(value)
        await elem.evaluate("el => el.blur()")
        return True

    async def _is_typeahead(self, elem: Locator) -> bool:
        role = (await elem.get_attribute("role") or "").lower()
        auto = (await elem.get_attribute("aria-autocomplete") or "").lower()
        return role == "combobox" or auto in {"list", "both"}

    async def _choose_typeahead_option(self, value: str) -> None:
        option = self.page.locator("[role='listbox'] [role='option'], .basic-typeahead__selectable").first
        try:
            await option.wait_for(state="visible", timeout=2000)
            await self._safe_click(option)
            return
        except Exception:
            pass
        try:
            await self.page.keyboard.press("ArrowDown")
            await self.page.keyboard.press("Enter")
        except Exception as e:
            logger.info(f"No typeahead suggestion selected for '{value}': {e}")

    async def _select_radio(self, q: ScreeningQuestion, target_value: str) -> bool:
        group = self.page.locator(q.selector)
        if await group.count() == 0:
            return False
        wanted = self._match_option_label(q, target_value)
        already = await group.evaluate(
            """(el, wanted) => Array.from(el.querySelectorAll("input[type=radio]")).some(radio => {
                if (!radio.checked) return false;
                const value = (radio.value || "").trim().toLowerCase();
                return value === wanted || value.startsWith(wanted + " ");
            })""",
            wanted,
        )
        if already:
            return True
        labels = group.locator("label")
        count = await labels.count()
        for i in range(count):
            label = labels.nth(i)
            text = " ".join((await label.inner_text()).split()).lower()
            if text == wanted or text.startswith(wanted + " ") or text.startswith(wanted + ","):
                await self._safe_click(label)
                checked = await label.evaluate("""el => {
                    const id = el.getAttribute('for');
                    const radio = id ? el.ownerDocument.getElementById(id) : el.querySelector("input[type=radio]");
                    return !!(radio && radio.checked);
                }""")
                if checked:
                    return True

        radios = group.locator("input[type='radio']")
        rcnt = await radios.count()
        for i in range(rcnt):
            radio = radios.nth(i)
            val = " ".join(((await radio.get_attribute("value")) or "").split()).lower()
            if val == wanted:
                try:
                    await radio.check(force=True)
                except Exception:
                    await radio.evaluate("el => { el.checked = true; el.dispatchEvent(new Event('change', { bubbles: true })); }")
                return True
        if rcnt == 1:
            try:
                await radios.first.check(force=True)
                return True
            except Exception:
                return False
        return False

    def _match_option_label(self, q: ScreeningQuestion, target_value: str) -> str:
        target = " ".join(target_value.strip().lower().split())
        for opt in q.options:
            label = " ".join(opt.label.strip().lower().split())
            value = " ".join(opt.value.strip().lower().split())
            if target and (target == label or target == value):
                return label or target
        for opt in q.options:
            label = " ".join(opt.label.strip().lower().split())
            value = " ".join(opt.value.strip().lower().split())
            if target and (target in label or target in value):
                return label or target
        return target

    async def _select_option(self, q: ScreeningQuestion, target_value: str) -> bool:
        sel = self.page.locator(q.selector).first
        if await sel.count() == 0:
            return False
        target = " ".join(target_value.strip().lower().split())
        chosen_value = None
        chosen_label = None
        if target == "primary" and q.options:
            chosen_value = q.options[0].value
            chosen_label = q.options[0].label.strip()
        else:
            partial = None
            for opt in q.options:
                label = " ".join(opt.label.strip().lower().split())
                value = " ".join(opt.value.strip().lower().split())
                if target and (label == target or value == target):
                    chosen_value = opt.value
                    chosen_label = opt.label.strip()
                    partial = None
                    break
                if partial is None and target and (target in label or target in value or label in target):
                    partial = opt
            if chosen_value is None and partial is not None:
                chosen_value = partial.value
                chosen_label = partial.label.strip()

        try:
            if chosen_value:
                await sel.select_option(value=chosen_value, timeout=3000)
            elif chosen_label:
                await sel.select_option(label=chosen_label, timeout=3000)
            elif q.options:
                await sel.select_option(value=q.options[0].value, timeout=3000)
            else:
                await sel.select_option(label=target_value, timeout=3000)
            return True
        except Exception as e:
            logger.warning(f"Could not select option '{target_value}': {e}")
            return False

    async def _checkbox_state(self, cb: Locator) -> Optional[bool]:
        try:
            if await cb.count() == 0 or not await cb.is_visible():
                return None
            return await cb.is_checked(timeout=2000)
        except Exception:
            return None

    async def _set_checkbox(self, q: ScreeningQuestion, target_value: str) -> bool:
        cb = self.page.locator(q.selector).first
        checked = await self._checkbox_state(cb)
        want_checked = target_value.lower() in {"true", "yes", "1"}
        if checked is None:
            return not want_checked
        if checked == want_checked:
            return True
        await self._safe_click(cb)
        checked = await self._checkbox_state(cb)
        if checked == want_checked:
            return True
        try:
            if want_checked:
                await cb.check(timeout=2000, force=True)
            else:
                await cb.uncheck(timeout=2000, force=True)
        except Exception:
            return False
        checked = await self._checkbox_state(cb)
        return checked == want_checked

    async def _safe_click(self, locator: Locator) -> None:
        try:
            await self.stealth.click_element(locator)
        except Exception:
            await locator.click(timeout=4000, force=True)

    async def visible_validation_errors(self) -> List[str]:
        modal = await resolve_modal(self.page)
        if await modal.count() == 0:
            return []
        errs = modal.locator(
            ".artdeco-inline-feedback--error, [data-test-form-element-error-messages]"
        )
        texts: List[str] = []
        count = await errs.count()
        for i in range(count):
            el = errs.nth(i)
            try:
                if not await el.is_visible():
                    continue
                txt = " ".join((await el.inner_text()).split())
            except Exception:
                continue
            if txt:
                texts.append(txt)
        if texts:
            logger.warning(f"Validation error(s) detected: {texts}")
        return texts

    async def has_validation_errors(self) -> bool:
        return len(await self.visible_validation_errors()) > 0

    async def close_completion_dialog(self) -> None:
        """Closes the post-submit dialog by clicking Not now, never Update profile."""
        try:
            for _ in range(6):
                not_now = self.page.get_by_role("button", name="Not now", exact=True)
                if await not_now.count() > 0 and await not_now.first.is_visible():
                    logger.info("Clicking 'Not now' on the application-sent popup.")
                    await not_now.first.click(timeout=4000)
                    await asyncio.sleep(0.4)
                    return
                done_btn = self.page.locator(
                    "[role='dialog'] button:has-text('Done'), button[aria-label*='Done' i]"
                ).first
                if await done_btn.count() > 0 and await done_btn.is_visible():
                    await done_btn.click(timeout=4000)
                    await asyncio.sleep(0.4)
                    return
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"Error closing completion dialog: {e}")

    async def _click_modal_button(self, selectors: str) -> bool:
        modal = await resolve_modal(self.page)
        buttons = modal.locator(selectors)
        count = await buttons.count()
        for i in range(count):
            button = buttons.nth(i)
            try:
                if await button.is_visible() and not await button.is_disabled():
                    await self._safe_click(button)
                    return True
            except Exception:
                continue
        return False

    async def _confirmation_visible(self) -> bool:
        try:
            return bool(await self.page.evaluate("""() => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    return style.display !== "none" && style.visibility !== "hidden" && el.getClientRects().length > 0;
                };
                const nodes = Array.from(document.querySelectorAll("[role='dialog'], .artdeco-modal"));
                const sent = nodes.some((el) => {
                    const text = el.innerText || "";
                    return /your application was sent/i.test(text) || /application submitted/i.test(text);
                });
                if (sent) return true;
                const buttons = Array.from(document.querySelectorAll("button")).filter(visible);
                const labels = buttons.map((button) => (button.innerText || "").trim().toLowerCase());
                return labels.includes("not now") && labels.includes("update profile");
            }"""))
        except Exception:
            return False

    async def _advance(self, selectors: str) -> Optional[str]:
        clicked = await self._click_modal_button(selectors)
        if not clicked:
            return "Action button was not clickable"
        await asyncio.sleep(0.8)
        if await self._confirmation_visible():
            return None
        errors = await self.visible_validation_errors()
        if not errors:
            return None

        logger.info("Validation failed after advancing. Filling the step again.")
        ok, reason = await self.fill_current_step()
        if not ok:
            return reason
        clicked = await self._click_modal_button(selectors)
        if not clicked:
            return "Action button was not clickable after fixing validation errors"
        await asyncio.sleep(0.8)
        errors = await self.visible_validation_errors()
        if errors and not await self._confirmation_visible():
            return "Encountered unresolvable form validation error: " + "; ".join(errors)
        return None

    async def capture_questions(self) -> List[dict]:
        """Walk every Easy Apply step, record questions, and discard without submitting."""
        captured: List[dict] = []
        seen = set()
        try:
            await self.page.wait_for_selector(
                ".jobs-easy-apply-modal, .jobs-easy-apply-content, [role='dialog']",
                timeout=10000,
            )
        except Exception:
            return captured

        for _ in range(self.max_steps):
            await self.stealth.random_delay(0.3, 0.6)
            if await self.save_prompt_open():
                await self.handle_save_prompt("discard")
                break
            if await self._confirmation_visible():
                break

            step_data = await extract_form_step(self.page, self.modal_selector)
            if (
                step_data.has_continue_applying
                and not step_data.has_next_button
                and not step_data.has_review_button
                and not step_data.has_submit_button
            ):
                if not await self._click_modal_button(CONTINUE_APPLYING_BUTTON):
                    break
                await asyncio.sleep(0.6)
                continue

            for question in step_data.questions:
                key = " ".join(question.question_text.split()).lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                solution = self.solver.solve_question(question)
                captured.append({
                    "question_text": " ".join(question.question_text.split()),
                    "input_type": question.input_type.value,
                    "options": [opt.label for opt in question.options],
                    "is_required": question.is_required,
                    "answer": solution.target_value,
                    "confidence": solution.confidence,
                    "covered": solution.confidence >= self.confidence_threshold and bool(solution.target_value),
                    "reasoning": solution.reasoning,
                })

            if step_data.has_submit_button:
                break

            ok, _reason = await self.fill_current_step()
            if not ok:
                break
            # Checking some boxes reveals another required field. Fill that before advancing.
            revealed = await extract_form_step(self.page, self.modal_selector)
            if len(revealed.questions) > len(step_data.questions):
                ok, _reason = await self.fill_current_step()
                if not ok:
                    break

            step_data = await extract_form_step(self.page, self.modal_selector)
            if step_data.has_submit_button or step_data.has_review_button:
                if step_data.has_review_button and not step_data.has_submit_button:
                    if not await self._click_modal_button(REVIEW_BUTTON):
                        break
                    await asyncio.sleep(0.6)
                    continue
                break

            if step_data.has_next_button:
                if not await self._click_modal_button(NEXT_BUTTON):
                    break
                await asyncio.sleep(0.6)
                if await self.visible_validation_errors():
                    break
                continue
            break

        await self.discard_modal()
        return captured

    async def process_easy_apply_modal(self) -> Tuple[NavigationResult, Optional[str]]:
        try:
            await self.page.wait_for_selector(
                ".jobs-easy-apply-modal, .jobs-easy-apply-content, [role='dialog']",
                timeout=10000,
            )
        except Exception:
            return NavigationResult.FAILED, "Easy Apply modal did not open"

        for step in range(self.max_steps):
            await self.stealth.random_delay(0.4, 0.8)
            if await self.save_prompt_open():
                logger.info("Save prompt was covering the application. Returning to the form.")
                await self.handle_save_prompt("return")
            if await self._confirmation_visible():
                await self.close_completion_dialog()
                return NavigationResult.SUBMITTED, None

            step_data: FormStepData = await extract_form_step(self.page, self.modal_selector)
            logger.info(
                f"Modal Step {step + 1}/{self.max_steps}: {len(step_data.questions)} questions "
                f"(Next={step_data.has_next_button}, Review={step_data.has_review_button}, "
                f"Submit={step_data.has_submit_button}, Safety={step_data.has_continue_applying})"
            )

            if (
                step_data.has_continue_applying
                and not step_data.has_next_button
                and not step_data.has_review_button
                and not step_data.has_submit_button
            ):
                logger.info("Passing the job-search safety reminder...")
                if not await self._click_modal_button(CONTINUE_APPLYING_BUTTON):
                    await self.discard_modal()
                    return NavigationResult.FAILED, "Could not continue past the safety reminder"
                await asyncio.sleep(0.8)
                continue

            ok, reason = await self.fill_current_step()
            if not ok:
                await self.discard_modal()
                return NavigationResult.DISCARDED, reason
            revealed = await extract_form_step(self.page, self.modal_selector)
            if len(revealed.questions) > len(step_data.questions):
                ok, reason = await self.fill_current_step()
                if not ok:
                    await self.discard_modal()
                    return NavigationResult.DISCARDED, reason

            step_data = await extract_form_step(self.page, self.modal_selector)
            if step_data.has_submit_button:
                logger.info("Clicking 'Submit application'...")
                failure = await self._advance(SUBMIT_BUTTON)
                if failure:
                    await self.discard_modal()
                    return NavigationResult.DISCARDED, failure
                confirmed = await self._confirmation_visible()
                if not confirmed:
                    try:
                        await self.page.wait_for_function(
                            """() => {
                                const nodes = Array.from(document.querySelectorAll("[role='dialog'], .artdeco-modal"));
                                const sent = nodes.some((el) => {
                                    const text = el.innerText || "";
                                    return /your application was sent/i.test(text) || /application submitted/i.test(text);
                                });
                                const labels = Array.from(document.querySelectorAll("button"))
                                    .map((button) => (button.innerText || "").trim().toLowerCase());
                                return sent || (labels.includes("not now") && labels.includes("update profile"));
                            }""",
                            timeout=8000,
                        )
                        confirmed = True
                    except Exception:
                        confirmed = await self._confirmation_visible()
                if not confirmed:
                    await self.discard_modal()
                    return NavigationResult.FAILED, "Submit was clicked but LinkedIn did not confirm the application"
                await self.close_completion_dialog()
                return NavigationResult.SUBMITTED, None

            if step_data.has_review_button:
                logger.info("Advancing through Review...")
                failure = await self._advance(REVIEW_BUTTON)
                if failure:
                    await self.discard_modal()
                    return NavigationResult.DISCARDED, failure
                continue

            if step_data.has_next_button:
                logger.info("Advancing through Next...")
                failure = await self._advance(NEXT_BUTTON)
                if failure:
                    await self.discard_modal()
                    return NavigationResult.DISCARDED, failure
                continue

            await self.discard_modal()
            return NavigationResult.FAILED, "No navigation action button detected in modal"

        await self.discard_modal()
        return NavigationResult.FAILED, f"Exceeded maximum allowable steps ({self.max_steps})"
