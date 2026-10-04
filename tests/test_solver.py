import pytest
from src.extractor import ScreeningQuestion, InputType, OptionItem
from src.solver import ScreeningSolver, QuestionSolution

def test_deterministic_profile_match_work_auth():
    solver = ScreeningSolver(profile_path="config/profile.json")
    q = ScreeningQuestion(
        field_id="auth",
        question_text="Are you legally authorized to work in the United States?",
        input_type=InputType.RADIO,
        options=[OptionItem(label="Yes", value="Yes"), OptionItem(label="No", value="No")],
        selector="#auth"
    )
    solution = solver.solve_question(q)
    assert solution.confidence == 1.0
    assert solution.action_type == "radio"
    assert solution.target_value == "Yes"

def test_deterministic_profile_match_experience():
    solver = ScreeningSolver(profile_path="config/profile.json")
    q = ScreeningQuestion(
        field_id="exp",
        question_text="How many years of work experience do you have with Python?",
        input_type=InputType.NUMERIC,
        selector="#exp"
    )
    solution = solver.solve_question(q)
    assert solution.confidence == 1.0
    assert solution.action_type == "type"
    assert solution.target_value == "5"

def test_confidence_discard_threshold():
    solver = ScreeningSolver(profile_path="config/profile.json")
    low_conf = QuestionSolution(action_type="type", target_value="Maybe", confidence=0.72, reasoning="Uncertain")
    high_conf = QuestionSolution(action_type="type", target_value="Yes", confidence=0.95, reasoning="Confident")
    assert solver.should_discard(low_conf, threshold=0.85) is True
    assert solver.should_discard(high_conf, threshold=0.85) is False
