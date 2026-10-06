from typing import List, Optional

from playwright.async_api import Locator, Page
from pydantic import BaseModel
from enum import Enum


class InputType(str, Enum):
    TEXT = "text"
    NUMERIC = "numeric"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    SELECT = "select"
    TEXTAREA = "textarea"


class OptionItem(BaseModel):
    label: str
    value: str


class ScreeningQuestion(BaseModel):
    field_id: str
    question_text: str
    input_type: InputType
    options: List[OptionItem] = []
    is_required: bool = False
    current_value: Optional[str] = None
    selector: str
    is_combobox: bool = False


class FormStepData(BaseModel):
    step_title: str
    questions: List[ScreeningQuestion] = []
    has_next_button: bool = False
    has_review_button: bool = False
    has_submit_button: bool = False
    has_continue_applying: bool = False


NEXT_BUTTON = (
    "button[aria-label='Continue to next step'], "
    "button[aria-label*='next step' i], "
    "button[data-easy-apply-next-button], "
    "button.artdeco-button--primary:has-text('Next'), "
    "footer button:has-text('Next'), "
    ".jobs-easy-apply-footer button:has-text('Next'), "
    ".artdeco-modal__actionbar button:has-text('Next')"
)

REVIEW_BUTTON = (
    "button[aria-label='Review your application'], "
    "button[aria-label*='review your application' i], "
    "button[data-live-test-easy-apply-review-button], "
    "button.artdeco-button--primary:has-text('Review'), "
    "footer button:has-text('Review'), "
    ".jobs-easy-apply-footer button:has-text('Review'), "
    ".artdeco-modal__actionbar button:has-text('Review')"
)

SUBMIT_BUTTON = (
    "button[aria-label='Submit application'], "
    "button[aria-label*='submit application' i], "
    "button[data-live-test-easy-apply-submit-button], "
    "button.artdeco-button--primary:has-text('Submit application'), "
    "footer button:has-text('Submit application'), "
    ".jobs-easy-apply-footer button:has-text('Submit application'), "
    ".artdeco-modal__actionbar button:has-text('Submit application')"
)

CONTINUE_APPLYING_BUTTON = (
    "button:has-text('Continue applying'), "
    "button[aria-label*='Continue applying' i]"
)


def clean_question_text(raw: str) -> str:
    parts = []
    for line in (raw or "").splitlines():
        piece = " ".join(line.split())
        if not piece or piece.lower() == "required":
            continue
        if parts and parts[-1].lower() == piece.lower():
            continue
        parts.append(piece)
    text = " ".join(parts) if parts else " ".join((raw or "").split())
    words = text.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if [word.lower() for word in words[:half]] == [word.lower() for word in words[half:]]:
            text = " ".join(words[:half])
    return text


async def _tag(locator: Locator, token: str) -> str:
    await locator.evaluate(
        "(el, token) => el.setAttribute('data-applier-field', token)",
        token,
    )
    return f"[data-applier-field='{token}']"


async def _group_question_text(control: Locator) -> str:
    raw = await control.evaluate("""el => {
        const group = el.closest(".additional_question, .form-group, .questions-container");
        if (!group) return "";
        const heading = group.querySelector(".assessment_question label, .assessment_question, .question-heading");
        if (heading && heading.innerText) return heading.innerText;
        const copy = group.cloneNode(true);
        copy.querySelectorAll("input, textarea, select, button, .error, label.error, .radio, .checkbox").forEach((node) => node.remove());
        return copy.innerText || "";
    }""")
    return clean_question_text(raw or "")


async def _control_label_raw(control: Locator) -> str:
    return await control.evaluate("""el => {
        const doc = el.ownerDocument;
        if (el.id) {
            const match = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (match && match.innerText) return match.innerText;
        }
        const wrapping = el.closest('label');
        if (wrapping && wrapping.innerText) return wrapping.innerText;
        return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    }""") or ""


async def _control_label(control: Locator, fallback: str = "") -> str:
    return clean_question_text(await _control_label_raw(control) or fallback)


async def _first_visible(modal: Locator, selectors: str) -> bool:
    buttons = modal.locator(selectors)
    count = await buttons.count()
    for i in range(count):
        button = buttons.nth(i)
        try:
            if await button.is_visible():
                return True
        except Exception:
            continue
    return False


async def resolve_modal(page: Page, modal_selector: str = ".jobs-easy-apply-modal") -> Locator:
    """Prefer the Easy Apply dialog, including when its buttons sit outside a footer tag."""
    preferred = page.locator(".jobs-easy-apply-modal, .jobs-easy-apply-content").first
    if await preferred.count() > 0:
        actions = preferred.locator(
            "button[aria-label='Continue to next step'], "
            "button[aria-label='Review your application'], "
            "button[aria-label='Submit application'], "
            "button[aria-label*='Continue applying' i], "
            "button.artdeco-button--primary, "
            "footer button"
        )
        if await actions.count() > 0:
            return preferred
        dialog = page.locator("[role='dialog']").filter(has=page.locator(".jobs-easy-apply-modal, .jobs-easy-apply-content"))
        if await dialog.count() > 0:
            return dialog.first
        return preferred

    candidates = page.locator(modal_selector)
    count = await candidates.count()
    for i in range(count):
        candidate = candidates.nth(i)
        try:
            if await candidate.is_visible():
                return candidate
        except Exception:
            continue
    if count > 0:
        return candidates.first
    return page.locator("[role='dialog']").first


async def extract_form_step(page: Page, modal_selector: str = ".jobs-easy-apply-modal") -> FormStepData:
    modal = await resolve_modal(page, modal_selector)

    title_elem = modal.locator("h2, h3, h1").first
    step_title = clean_question_text(await title_elem.inner_text()) if await title_elem.count() > 0 else "Screening"

    questions: List[ScreeningQuestion] = []

    fieldsets = modal.locator("fieldset")
    fcnt = await fieldsets.count()
    for i in range(fcnt):
        fs = fieldsets.nth(i)
        radios = fs.locator("input[type='radio']")
        rcnt = await radios.count()
        if rcnt == 0:
            continue

        legend = fs.locator("legend").first
        raw_text = (await legend.inner_text()) if await legend.count() > 0 else "Radio Question"
        text = clean_question_text(raw_text)
        options: List[OptionItem] = []
        name = await radios.first.get_attribute("name") or f"radio_{i}"
        current_value = None

        for r_idx in range(rcnt):
            radio = radios.nth(r_idx)
            val = await radio.get_attribute("value") or ""
            lbl_text = await _control_label(radio, val)
            options.append(OptionItem(label=lbl_text or val, value=val or lbl_text))
            if await radio.is_checked():
                current_value = val or lbl_text

        is_req = (
            "*" in raw_text
            or "required" in raw_text.lower()
            or await fs.locator("[required], [aria-required='true']").count() > 0
        )
        questions.append(ScreeningQuestion(
            field_id=f"fieldset_{i}_{name}",
            question_text=text,
            input_type=InputType.RADIO,
            options=options,
            is_required=is_req,
            current_value=current_value,
            selector=await _tag(fs, f"radio-{i}"),
        ))

    loose_radios = modal.locator("input[type='radio']")
    seen_names = set()
    loose_count = await loose_radios.count()
    group_index = 0
    for i in range(loose_count):
        radio = loose_radios.nth(i)
        if await radio.evaluate("el => !!el.closest('fieldset')"):
            continue
        name = await radio.get_attribute("name") or ""
        if not name or name in seen_names or name == "confirm_availability":
            continue
        try:
            if not await radio.is_visible():
                continue
        except Exception:
            continue
        seen_names.add(name)
        group = modal.locator(f"input[type='radio'][name='{name}']")
        options = []
        current_value = None
        for r_idx in range(await group.count()):
            item = group.nth(r_idx)
            val = await item.get_attribute("value") or ""
            lbl_text = await _control_label(item, val)
            options.append(OptionItem(label=lbl_text or val, value=val or lbl_text))
            try:
                if await item.is_checked(timeout=1000):
                    current_value = val or lbl_text
            except Exception:
                pass
        raw_text = await _group_question_text(radio)
        text = raw_text or "Radio Question"
        is_req = (
            "required" in text.lower()
            or "*" in text
            or await group.locator("[required], [aria-required='true']").count() > 0
        )
        token = f"loose-radio-{group_index}"
        await radio.evaluate(
            """(el, token) => {
                const group = el.closest(".additional_question, .form-group") || el.parentElement;
                if (group) group.setAttribute("data-applier-field", token);
            }""",
            token,
        )
        questions.append(ScreeningQuestion(
            field_id=f"radio_group_{group_index}_{name}",
            question_text=text,
            input_type=InputType.RADIO,
            options=options,
            is_required=is_req,
            current_value=current_value,
            selector=f"[data-applier-field='{token}']",
        ))
        group_index += 1

    selects = modal.locator("select")
    scnt = await selects.count()
    for i in range(scnt):
        sel = selects.nth(i)
        try:
            if not await sel.is_visible():
                continue
        except Exception:
            continue
        sel_id = await sel.get_attribute("id") or f"select_{i}"
        raw_text = await _control_label_raw(sel) or "Dropdown Question"
        q_text = clean_question_text(raw_text)
        try:
            raw_options = await sel.evaluate("""
                el => Array.from(el.options)
                    .filter(o => o.value && !o.text.toLowerCase().includes('select an option'))
                    .map(o => ({label: o.text.trim(), value: o.value}))
            """)
            options = [OptionItem(label=o["label"], value=o["value"]) for o in raw_options]
        except Exception:
            options = []

        is_req = (
            "*" in raw_text
            or "required" in raw_text.lower()
            or await sel.get_attribute("required") is not None
            or (await sel.get_attribute("aria-required") or "").lower() == "true"
        )
        current = await sel.input_value()
        questions.append(ScreeningQuestion(
            field_id=sel_id,
            question_text=q_text,
            input_type=InputType.SELECT,
            options=options,
            is_required=is_req,
            current_value=current or None,
            selector=await _tag(sel, f"select-{i}"),
        ))

    checkboxes = modal.locator("input[type='checkbox']")
    ccnt = await checkboxes.count()
    for i in range(ccnt):
        cb = checkboxes.nth(i)
        try:
            if not await cb.is_visible():
                continue
            is_checked = await cb.is_checked(timeout=2000)
        except Exception:
            continue
        cb_id = await cb.get_attribute("id") or f"cb_{i}"
        raw_text = await _control_label_raw(cb) or "Checkbox"
        q_text = clean_question_text(raw_text)
        is_req = (
            "*" in raw_text
            or "required" in raw_text.lower()
            or await cb.get_attribute("required") is not None
            or (await cb.get_attribute("aria-required") or "").lower() == "true"
        )
        questions.append(ScreeningQuestion(
            field_id=cb_id,
            question_text=q_text,
            input_type=InputType.CHECKBOX,
            options=[OptionItem(label="checked", value="true"), OptionItem(label="unchecked", value="false")],
            is_required=is_req,
            current_value="true" if is_checked else "false",
            selector=await _tag(cb, f"check-{i}"),
        ))

    inputs = modal.locator("input[type='text'], input[type='number'], input[type='tel'], input[type='email'], input:not([type])")
    icnt = await inputs.count()
    for i in range(icnt):
        inp = inputs.nth(i)
        itype = (await inp.get_attribute("type") or "text").lower()
        if itype in {"radio", "checkbox", "hidden", "file", "submit", "button"}:
            continue
        try:
            if not await inp.is_visible():
                continue
        except Exception:
            continue
        raw_group = await _group_question_text(inp)
        inp_id = await inp.get_attribute("id") or f"inp_{i}"
        raw_text = raw_group or await _control_label_raw(inp) or "Text Question"
        q_text = clean_question_text(raw_text)
        curr_val = await inp.input_value()
        role = (await inp.get_attribute("role") or "").lower()
        autocomplete = (await inp.get_attribute("aria-autocomplete") or "").lower()
        is_req = (
            "*" in raw_text
            or "required" in raw_text.lower()
            or await inp.get_attribute("required") is not None
            or (await inp.get_attribute("aria-required") or "").lower() == "true"
        )
        questions.append(ScreeningQuestion(
            field_id=inp_id,
            question_text=q_text,
            input_type=InputType.NUMERIC if itype == "number" else InputType.TEXT,
            is_required=is_req,
            current_value=curr_val or None,
            selector=await _tag(inp, f"text-{i}"),
            is_combobox=role == "combobox" or autocomplete in {"list", "both"},
        ))

    tas = modal.locator("textarea")
    tcnt = await tas.count()
    for i in range(tcnt):
        ta = tas.nth(i)
        try:
            if not await ta.is_visible():
                continue
        except Exception:
            continue
        ta_id = await ta.get_attribute("id") or f"ta_{i}"
        raw_group = await _group_question_text(ta)
        ta_name = (await ta.get_attribute("name") or "").lower()
        if "recaptcha" in ta_id.lower() or "recaptcha" in ta_name:
            continue
        raw_text = raw_group or await _control_label_raw(ta) or "Textarea"
        q_text = clean_question_text(raw_text)
        curr_val = await ta.input_value()
        is_req = (
            "*" in raw_text
            or "required" in raw_text.lower()
            or await ta.get_attribute("required") is not None
            or (await ta.get_attribute("aria-required") or "").lower() == "true"
        )
        questions.append(ScreeningQuestion(
            field_id=ta_id,
            question_text=q_text,
            input_type=InputType.TEXTAREA,
            is_required=is_req,
            current_value=curr_val or None,
            selector=await _tag(ta, f"area-{i}"),
        ))

    return FormStepData(
        step_title=step_title,
        questions=questions,
        has_next_button=await _first_visible(modal, NEXT_BUTTON),
        has_review_button=await _first_visible(modal, REVIEW_BUTTON),
        has_submit_button=await _first_visible(modal, SUBMIT_BUTTON),
        has_continue_applying=await _first_visible(modal, CONTINUE_APPLYING_BUTTON),
    )
