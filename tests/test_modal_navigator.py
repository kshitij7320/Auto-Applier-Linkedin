import os
import time

import pytest
from playwright.async_api import async_playwright
from src.extractor import InputType, ScreeningQuestion
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
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
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


LIVE_MODAL_HTML = """
<button id="decoy-next" aria-label="Continue to next step">Next</button>
<div class="artdeco-inline-feedback--error" style="display:none">Please enter a valid answer</div>
<div class="jobs-easy-apply-modal" role="dialog">
  <h2>Additional Questions</h2>
  <div class="artdeco-inline-feedback--error" style="display:none">Please enter a valid answer</div>
  <div class="fb-dash-form-element">
    <fieldset>
      <legend>Are you comfortable working in an onsite setting?<span class="visually-hidden">Required</span></legend>
      <div>
        <input id="onsite-yes" type="radio" name="onsite" value="Yes" style="position:absolute;width:1px;height:1px;opacity:0">
        <label for="onsite-yes" style="display:inline-block;padding:8px">Yes</label>
      </div>
      <div>
        <input id="onsite-no" type="radio" name="onsite" value="No" style="position:absolute;width:1px;height:1px;opacity:0">
        <label for="onsite-no" style="display:inline-block;padding:8px">No</label>
      </div>
    </fieldset>
  </div>
  <div class="fb-dash-form-element">
    <fieldset>
      <legend>We must fill this position urgently. Can you start immediately?<span class="visually-hidden">Required</span></legend>
      <div>
        <input id="start-yes" type="radio" name="start" value="Yes" style="position:absolute;width:1px;height:1px;opacity:0">
        <label for="start-yes" style="display:inline-block;padding:8px">Yes</label>
      </div>
      <div>
        <input id="start-no" type="radio" name="start" value="No" style="position:absolute;width:1px;height:1px;opacity:0">
        <label for="start-no" style="display:inline-block;padding:8px">No</label>
      </div>
    </fieldset>
  </div>
  <footer>
    <button id="real-next" class="artdeco-button--primary" aria-label="Continue to next step">Next</button>
  </footer>
</div>
<script>
  document.getElementById("decoy-next").addEventListener("click", () => {
    document.body.dataset.decoy = "1";
  });
  document.getElementById("real-next").addEventListener("click", () => {
    const onsite = document.querySelector("input[name='onsite']:checked");
    const start = document.querySelector("input[name='start']:checked");
    const modal = document.querySelector(".jobs-easy-apply-modal");
    modal.querySelectorAll(".artdeco-inline-feedback--error").forEach((node) => {
      if (node.style.display !== "none") node.remove();
    });
    if (!onsite || !start) {
      const err = document.createElement("div");
      err.className = "artdeco-inline-feedback--error";
      err.textContent = "Please enter a valid answer";
      modal.querySelector("footer").before(err);
      return;
    }
    const footer = modal.querySelector("footer");
    footer.innerHTML = '<button id="real-submit" aria-label="Submit application">Submit application</button>';
    document.getElementById("real-submit").addEventListener("click", () => {
      modal.querySelector("h2").textContent = "Application submitted";
    });
  });
</script>
"""

SAFETY_MODAL_HTML = """
<div class="jobs-easy-apply-modal" role="dialog">
  <h2>Job search safety reminder</h2>
  <button id="continue-applying">Continue applying</button>
</div>
<script>
  document.getElementById("continue-applying").addEventListener("click", () => {
    const modal = document.querySelector(".jobs-easy-apply-modal");
    modal.innerHTML = '<h2>Review</h2><footer><button id="real-submit" aria-label="Submit application">Submit application</button></footer>';
    document.getElementById("real-submit").addEventListener("click", () => {
      modal.querySelector("h2").textContent = "Application submitted";
    });
  });
</script>
"""


@pytest.mark.asyncio
async def test_modal_submits_when_controls_match_live_linkedin_dom():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(LIVE_MODAL_HTML)

        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver, max_steps=4)
        result, reason = await navigator.process_easy_apply_modal()

        assert result == NavigationResult.SUBMITTED, reason
        assert await page.locator("h2").inner_text() == "Application submitted"
        assert await page.locator("input[name='onsite'][value='Yes']").is_checked()
        assert await page.locator("input[name='start'][value='Yes']").is_checked()
        assert (await page.evaluate("() => document.body.dataset.decoy || ''")) == ""
        await browser.close()


@pytest.mark.asyncio
async def test_capture_records_questions_without_submitting():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(LIVE_MODAL_HTML)

        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver, max_steps=4)
        captured = await navigator.capture_questions()

        texts = [item["question_text"] for item in captured]
        assert any("onsite" in text.lower() for text in texts)
        assert any("start immediately" in text.lower() for text in texts)
        assert all(item["covered"] for item in captured)
        assert await page.locator("h2").inner_text() != "Application submitted"
        await browser.close()


@pytest.mark.asyncio
async def test_safety_reminder_is_continued_before_submit():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(SAFETY_MODAL_HTML)

        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver, max_steps=4)
        result, reason = await navigator.process_easy_apply_modal()
        assert result == NavigationResult.SUBMITTED, reason
        await browser.close()


SAVE_PROMPT_HTML = """
<div class="jobs-easy-apply-modal" role="dialog">
  <h2>Review your application</h2>
  <button id="modal-x" aria-label="Dismiss">Close</button>
  <footer><button id="submit" aria-label="Submit application">Submit application</button></footer>
</div>
<div id="save-dialog" role="dialog">
  <h2>Save this application?</h2>
  <button id="save-x" aria-label="Dismiss">x</button>
  <button id="discard">Discard</button>
  <button id="save">Save</button>
</div>
<script>
  document.getElementById("discard").addEventListener("click", () => {
    document.body.dataset.choice = "discard";
    document.getElementById("save-dialog").remove();
    document.querySelector(".jobs-easy-apply-modal").remove();
  });
  document.getElementById("save").addEventListener("click", () => {
    document.body.dataset.choice = "save";
  });
  document.getElementById("save-x").addEventListener("click", () => {
    document.body.dataset.choice = "return";
    document.getElementById("save-dialog").remove();
  });
  document.getElementById("modal-x").addEventListener("click", () => {
    if (!document.getElementById("save-dialog")) {
      const dialog = document.createElement("div");
      dialog.id = "save-dialog";
      dialog.setAttribute("role", "dialog");
      dialog.innerHTML = '<h2>Save this application?</h2><button id="discard">Discard</button><button id="save">Save</button>';
      document.body.appendChild(dialog);
      document.getElementById("discard").addEventListener("click", () => {
        document.body.dataset.choice = "discard";
        dialog.remove();
        document.querySelector(".jobs-easy-apply-modal").remove();
      });
    }
  });
  document.getElementById("submit").addEventListener("click", () => {
    document.querySelector(".jobs-easy-apply-modal h2").textContent = "Application submitted";
  });
</script>
"""


@pytest.mark.asyncio
async def test_save_prompt_discard_does_not_click_save():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(SAVE_PROMPT_HTML)
        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver)
        assert await navigator.handle_save_prompt("discard") is True
        assert await page.evaluate("() => document.body.dataset.choice") == "discard"
        assert await page.locator("h2:has-text('Save this application?')").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_apply_returns_from_save_prompt_and_submits():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(SAVE_PROMPT_HTML)
        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver, max_steps=3)
        result, reason = await navigator.process_easy_apply_modal()
        assert result == NavigationResult.SUBMITTED, reason
        assert await page.evaluate("() => document.body.dataset.choice || ''") == "return"
        await browser.close()


@pytest.mark.asyncio
async def test_application_sent_popup_clicks_not_now():
    html = """
    <div role="dialog">
      <div class="t-16 t-bold">Your application was sent to Saatvik Agro!</div>
      <button id="not-now">Not now</button>
      <button id="update">Update profile</button>
    </div>
    <script>
      document.getElementById("not-now").addEventListener("click", () => {
        document.body.dataset.choice = "not-now";
        document.querySelector("[role='dialog']").remove();
      });
      document.getElementById("update").addEventListener("click", () => {
        document.body.dataset.choice = "update";
      });
    </script>
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver)
        assert await navigator._confirmation_visible() is True
        await navigator.close_completion_dialog()
        assert await page.evaluate("() => document.body.dataset.choice") == "not-now"
        assert await page.locator("text=Update profile").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_hidden_page_errors_do_not_count_as_validation_failures():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <div class="artdeco-inline-feedback--error" style="display:none">Please enter a valid answer</div>
            <div class="jobs-easy-apply-modal" role="dialog">
              <h2>Contact info</h2>
              <div class="artdeco-inline-feedback--error" style="display:none"></div>
              <footer><button aria-label="Continue to next step">Next</button></footer>
            </div>
            """
        )
        stealth = StealthController(page)
        solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
        navigator = ModalNavigator(page=page, stealth=stealth, solver=solver)
        assert await navigator.visible_validation_errors() == []
        await browser.close()


@pytest.mark.asyncio
async def test_hidden_checkbox_does_not_block_the_form():
    html = """
    <input id="hidden-box" type="checkbox" data-applier-field="check-0" checked style="display:none">
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        navigator = ModalNavigator(
            page=page,
            stealth=StealthController(page),
            solver=ScreeningSolver(profile_path="tests/fixtures/mock_profile.json"),
        )
        question = ScreeningQuestion(
            field_id="hidden-box",
            question_text="Hidden preference",
            input_type=InputType.CHECKBOX,
            selector="[data-applier-field='check-0']",
        )
        started = time.monotonic()
        left_alone = await navigator._set_checkbox(question, "false")
        assert time.monotonic() - started < 5
        assert left_alone is True
        assert await page.locator("#hidden-box").is_checked()
        await browser.close()

