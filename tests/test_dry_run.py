import os
import pytest
from playwright.async_api import async_playwright
from src.db import DatabaseTracker
from src.stealth import StealthController
from src.solver import ScreeningSolver
from src.modal_navigator import ModalNavigator, NavigationResult

@pytest.mark.asyncio
async def test_full_pipeline_dry_run():
    fixture_path = os.path.abspath("tests/fixtures/mock_modal.html")
    db_path = "storage/test_dry_run.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseTracker(db_path)
    db.init_db()
    solver = ScreeningSolver(profile_path="config/profile.json", db_tracker=db)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file://{fixture_path}")

        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=solver,
            max_steps=5,
            confidence_threshold=0.85
        )

        # 1. Test filling
        ok, reason = await navigator.fill_current_step()
        assert ok is True
        assert reason is None

        # 2. Verify all inputs got populated accurately
        phone_val = await page.locator("#phone-input").input_value()
        assert phone_val == "1234567890"  # Pre-existing value preserved

        exp_val = await page.locator("#experience-input").input_value()
        assert exp_val == "5"

        auth_val = await page.locator("input[name='auth-radio'][value='Yes']").is_checked()
        assert auth_val is True

        clearance_val = await page.locator("#clearance-select").input_value()
        assert clearance_val == "No"

        terms_val = await page.locator("#terms-agree").is_checked()
        assert terms_val is True

        # 3. Verify SQLite cached QA records & duplicate suppression
        assert db.get_session_applied_count() == 0
        db.record_application("mock-job-001", "Senior Python Engineer", "Acme AI", "Remote", "APPLIED")
        assert db.get_session_applied_count() == 1
        assert db.is_already_applied("mock-job-001") is True

        # Cached screening questions verification
        from src.extractor import ScreeningQuestion, InputType
        test_q = ScreeningQuestion(
            field_id="auth_test",
            question_text="Are you legally authorized to work in the United States? *",
            input_type=InputType.RADIO,
            selector="input"
        )
        q_hash = solver._hash_question(test_q)
        cached = db.get_cached_answer(q_hash)
        assert cached is not None
        assert cached["target_value"] == "Yes"

        await browser.close()

    if os.path.exists(db_path):
        os.remove(db_path)
