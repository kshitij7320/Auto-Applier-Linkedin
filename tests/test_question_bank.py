import json

from src.extractor import InputType, OptionItem, ScreeningQuestion
from src.solver import ScreeningSolver


def test_saved_answer_covers_a_question_the_profile_does_not_know(tmp_path):
    bank_path = tmp_path / "questions.json"
    bank_path.write_text(json.dumps({
        "What is your favorite editor?": {
            "question_text": "What is your favorite editor?",
            "input_type": "text",
            "options": [],
            "is_required": True,
            "answer": "VS Code",
            "confidence": 0.95,
            "covered": True,
            "reasoning": "saved",
        }
    }), encoding="utf-8")
    solver = ScreeningSolver(
        profile_path="tests/fixtures/mock_profile.json",
        question_bank_path=str(bank_path),
        gemini_api_key=None,
    )
    question = ScreeningQuestion(
        field_id="editor",
        question_text="What is your favorite editor?",
        input_type=InputType.TEXT,
        selector="#editor",
        is_required=True,
    )
    solution = solver.solve_question(question)
    assert solution.target_value == "VS Code"
    assert solution.confidence >= 0.85


def test_email_address_is_not_answered_with_the_city():
    solver = ScreeningSolver(
        profile_path="tests/fixtures/mock_profile.json",
        gemini_api_key=None,
    )
    question = ScreeningQuestion(
        field_id="email",
        question_text="Email address",
        input_type=InputType.SELECT,
        options=[OptionItem(label="test@example.com", value="test@example.com")],
        selector="#email",
        is_required=True,
    )
    assert solver.solve_question(question).target_value == "test@example.com"


def test_catalog_seed_prepares_every_required_question(tmp_path):
    solver = ScreeningSolver(
        profile_path="config/profile.json",
        question_bank_path=str(tmp_path / "questions.json"),
        gemini_api_key=None,
    )
    solver.question_bank.seed(solver.preview_answer)
    assert solver.question_bank.uncovered_required() == []
