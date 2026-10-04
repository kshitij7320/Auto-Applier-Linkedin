from enum import Enum
from typing import List, Optional
from pydantic import BaseModel
from playwright.async_api import Page, Locator

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

class FormStepData(BaseModel):
    step_title: str
    questions: List[ScreeningQuestion] = []
    has_next_button: bool = False
    has_review_button: bool = False
    has_submit_button: bool = False

async def extract_form_step(page: Page, modal_selector: str = ".jobs-easy-apply-modal") -> FormStepData:
    modal = page.locator(modal_selector).first
    if await modal.count() == 0:
        modal = page.locator("[role='dialog']").first

    title_elem = modal.locator("h2, h3, h1").first
    step_title = (await title_elem.inner_text()).strip() if await title_elem.count() > 0 else "Screening"

    questions: List[ScreeningQuestion] = []

    # 1. Radio groups (usually in fieldset)
    fieldsets = modal.locator("fieldset")
    fcnt = await fieldsets.count()
    for i in range(fcnt):
        fs = fieldsets.nth(i)
        radios = fs.locator("input[type='radio']")
        rcnt = await radios.count()
        if rcnt > 0:
            legend = fs.locator("legend").first
            text = (await legend.inner_text()).strip() if await legend.count() > 0 else "Radio Question"
            options: List[OptionItem] = []
            name = await radios.first.get_attribute("name") or f"radio_{i}"

            for r_idx in range(rcnt):
                r = radios.nth(r_idx)
                val = await r.get_attribute("value") or ""
                label_elem = fs.locator(f"label:has(input[value='{val}']), label[for='{await r.get_attribute('id')}']")
                lbl_text = (await label_elem.first.inner_text()).strip() if await label_elem.count() > 0 else val
                options.append(OptionItem(label=lbl_text, value=val))

            questions.append(ScreeningQuestion(
                field_id=f"fieldset_{i}_{name}",
                question_text=text,
                input_type=InputType.RADIO,
                options=options,
                is_required="*" in text or await fs.locator("[required]").count() > 0,
                selector=f"{modal_selector} fieldset:nth-of-type({i+1})"
            ))

    # 2. Select elements
    selects = modal.locator("select")
    scnt = await selects.count()
    for i in range(scnt):
        sel = selects.nth(i)
        sel_id = await sel.get_attribute("id") or f"select_{i}"
        label_elem = modal.locator(f"label[for='{sel_id}']").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await sel.get_attribute("aria-label") or "Dropdown Question")
        opts = sel.locator("option")
        opt_cnt = await opts.count()
        options = []
        for o_idx in range(opt_cnt):
            opt = opts.nth(o_idx)
            val = await opt.get_attribute("value") or ""
            otext = (await opt.inner_text()).strip()
            if val and otext and "select an option" not in otext.lower():
                options.append(OptionItem(label=otext, value=val))

        questions.append(ScreeningQuestion(
            field_id=sel_id,
            question_text=q_text,
            input_type=InputType.SELECT,
            options=options,
            is_required="*" in q_text or await sel.get_attribute("required") is not None,
            selector=f"#{sel_id}" if await sel.get_attribute("id") else f"{modal_selector} select >> nth={i}"
        ))

    # 3. Checkboxes
    checkboxes = modal.locator("input[type='checkbox']")
    ccnt = await checkboxes.count()
    for i in range(ccnt):
        cb = checkboxes.nth(i)
        cb_id = await cb.get_attribute("id") or f"cb_{i}"
        label_elem = modal.locator(f"label[for='{cb_id}'], label:has(#{cb_id})").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await cb.get_attribute("aria-label") or "Checkbox")
        is_checked = await cb.is_checked()
        questions.append(ScreeningQuestion(
            field_id=cb_id,
            question_text=q_text,
            input_type=InputType.CHECKBOX,
            options=[OptionItem(label="checked", value="true"), OptionItem(label="unchecked", value="false")],
            is_required="*" in q_text or await cb.get_attribute("required") is not None,
            current_value="true" if is_checked else "false",
            selector=f"#{cb_id}" if await cb.get_attribute("id") else f"{modal_selector} input[type='checkbox'] >> nth={i}"
        ))

    # 4. Text and numeric inputs
    inputs = modal.locator("input[type='text'], input[type='number'], input[type='tel'], input:not([type])")
    icnt = await inputs.count()
    for i in range(icnt):
        inp = inputs.nth(i)
        itype = await inp.get_attribute("type") or "text"
        inp_id = await inp.get_attribute("id") or f"inp_{i}"
        label_elem = modal.locator(f"label[for='{inp_id}'], label:has(#{inp_id})").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await inp.get_attribute("aria-label") or "Text Question")
        curr_val = await inp.input_value()
        questions.append(ScreeningQuestion(
            field_id=inp_id,
            question_text=q_text,
            input_type=InputType.NUMERIC if itype == "number" else InputType.TEXT,
            is_required="*" in q_text or await inp.get_attribute("required") is not None,
            current_value=curr_val or None,
            selector=f"#{inp_id}" if await inp.get_attribute("id") else f"{modal_selector} input >> nth={i}"
        ))

    # 5. Textareas
    tas = modal.locator("textarea")
    tcnt = await tas.count()
    for i in range(tcnt):
        ta = tas.nth(i)
        ta_id = await ta.get_attribute("id") or f"ta_{i}"
        label_elem = modal.locator(f"label[for='{ta_id}'], label:has(#{ta_id})").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await ta.get_attribute("aria-label") or "Textarea")
        curr_val = await ta.input_value()
        questions.append(ScreeningQuestion(
            field_id=ta_id,
            question_text=q_text,
            input_type=InputType.TEXTAREA,
            is_required="*" in q_text or await ta.get_attribute("required") is not None,
            current_value=curr_val or None,
            selector=f"#{ta_id}" if await ta.get_attribute("id") else f"{modal_selector} textarea >> nth={i}"
        ))

    # Detect action buttons
    has_next = await modal.locator("button:has-text('Next'), button[aria-label*='next' i]").count() > 0
    has_review = await modal.locator("button:has-text('Review'), button[aria-label*='review' i]").count() > 0
    has_submit = await modal.locator("button:has-text('Submit application'), button[aria-label*='Submit application' i]").count() > 0

    return FormStepData(
        step_title=step_title,
        questions=questions,
        has_next_button=has_next,
        has_review_button=has_review,
        has_submit_button=has_submit
    )
