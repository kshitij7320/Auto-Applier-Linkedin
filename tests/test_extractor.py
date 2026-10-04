import os
import pytest
from playwright.async_api import async_playwright
from src.extractor import extract_form_step, InputType

@pytest.mark.asyncio
async def test_form_extractor_parses_all_input_types():
    fixture_path = os.path.abspath("tests/fixtures/mock_modal.html")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file://{fixture_path}")

        step_data = await extract_form_step(page, modal_selector=".jobs-easy-apply-modal")
        assert step_data.has_next_button is True
        assert step_data.has_submit_button is False
        assert len(step_data.questions) == 6

        q_map = {q.input_type: q for q in step_data.questions}
        assert InputType.TEXT in q_map
        assert "Mobile phone number" in q_map[InputType.TEXT].question_text
        assert q_map[InputType.TEXT].current_value == "1234567890"

        assert InputType.RADIO in q_map
        assert "legally authorized" in q_map[InputType.RADIO].question_text
        assert len(q_map[InputType.RADIO].options) == 2

        assert InputType.NUMERIC in q_map
        assert "years of work experience do you have with Python" in q_map[InputType.NUMERIC].question_text

        assert InputType.SELECT in q_map
        assert "Secret clearance" in q_map[InputType.SELECT].question_text

        assert InputType.CHECKBOX in q_map
        assert "privacy statement" in q_map[InputType.CHECKBOX].question_text

        assert InputType.TEXTAREA in q_map
        assert "Additional notes" in q_map[InputType.TEXTAREA].question_text

        await browser.close()
