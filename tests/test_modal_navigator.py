import os
import pytest
from playwright.async_api import async_playwright
from src.stealth import StealthController
from src.solver import ScreeningSolver
from src.modal_navigator import ModalNavigator, NavigationResult

@pytest.mark.asyncio
async def test_modal_navigation_fills_and_advances():
    fixture_path = os.path.abspath("tests/fixtures/mock_modal.html")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file://{fixture_path}")

        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="config/profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver, max_steps=4)

        result, reason = await navigator.fill_current_step()
        assert result is True
        assert reason is None

        # Verify radio button got checked
        auth_radio = page.locator("input[name='auth-radio'][value='Yes']")
        assert await auth_radio.is_checked()

        # Verify experience input received value
        exp_val = await page.locator("#experience-input").input_value()
        assert exp_val == "5"

        # Verify select got set
        clearance_val = await page.locator("#clearance-select").input_value()
        assert clearance_val == "No"

        # Verify checkbox got checked
        terms_cb = page.locator("#terms-agree")
        assert await terms_cb.is_checked()

        await browser.close()

@pytest.mark.asyncio
async def test_modal_navigator_discards_on_low_confidence_required_question():
    fixture_path = os.path.abspath("tests/fixtures/mock_modal.html")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file://{fixture_path}")

        stealth = StealthController(page)
        # Empty profile causes required custom questions to yield low confidence
        solver = ScreeningSolver(profile_path="config/profile.json", gemini_api_key=None)
        # Mock solve_question to return low confidence on a required question
        orig_solve = solver.solve_question
        def low_conf_solve(q):
            if "Secret clearance" in q.question_text:
                from src.solver import QuestionSolution
                return QuestionSolution(action_type="select", target_value="Yes", confidence=0.6, reasoning="Unsure")
            return orig_solve(q)
        solver.solve_question = low_conf_solve

        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver, max_steps=4)
        result, reason = await navigator.fill_current_step()
        assert result is False
        assert "Low confidence" in reason
        assert "Secret clearance" in reason

        await browser.close()

