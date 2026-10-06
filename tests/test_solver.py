import pytest
from src.extractor import ScreeningQuestion, InputType, OptionItem
from src.solver import ScreeningSolver, QuestionSolution

def test_deterministic_profile_match_work_auth():
    solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
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

def test_primary_skills_answer_zero_years_for_a_fresher():
    solver = ScreeningSolver(profile_path="config/profile.json")
    for skill in ["React.js", "Node.js", "Express.js", "MongoDB"]:
        question = ScreeningQuestion(
            field_id=skill,
            question_text=f"How many years of work experience do you have with {skill}?",
            input_type=InputType.NUMERIC,
            selector="#years",
        )
        solution = solver.solve_question(question)
        assert solution.target_value == "0"

    select_question = ScreeningQuestion(
        field_id="react-select",
        question_text="How many years of work experience do you have with React.js?",
        input_type=InputType.SELECT,
        options=[
            OptionItem(label="3-5 years", value="3"),
            OptionItem(label="Fresher / less than 1 year", value="0"),
        ],
        selector="#react-select",
    )
    assert "fresher" in solver.solve_question(select_question).target_value.lower()


def test_current_salary_is_zero_and_expected_salary_stays_set():
    solver = ScreeningSolver(profile_path="config/profile.json")
    current = ScreeningQuestion(
        field_id="current-ctc",
        question_text="What is your current CTC?",
        input_type=InputType.NUMERIC,
        selector="#current-ctc",
    )
    expected = ScreeningQuestion(
        field_id="expected-ctc",
        question_text="What is your expected CTC?",
        input_type=InputType.NUMERIC,
        selector="#expected-ctc",
    )
    assert solver.solve_question(current).target_value == "0"
    assert solver.solve_question(expected).target_value == "360000"
    text_expected = ScreeningQuestion(
        field_id="expected-text",
        question_text="What is your expected salary?",
        input_type=InputType.TEXT,
        selector="#expected-text",
    )
    assert solver.solve_question(text_expected).target_value == "360000 to 400000"
    band = ScreeningQuestion(
        field_id="expected-band",
        question_text="Expected CTC",
        input_type=InputType.SELECT,
        options=[
            OptionItem(label="2-3 LPA", value="2-3"),
            OptionItem(label="3-4 LPA", value="3-4"),
            OptionItem(label="6-8 LPA", value="6-8"),
        ],
        selector="#expected-band",
    )
    assert solver.solve_question(band).target_value == "3-4 LPA"


def test_deterministic_profile_match_experience():
    solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
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

def test_onsite_and_profile_links_resolve_without_llm():
    solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
    onsite = ScreeningQuestion(
        field_id="onsite",
        question_text="Are you comfortable working in an onsite setting?",
        input_type=InputType.RADIO,
        options=[OptionItem(label="Yes", value="Yes"), OptionItem(label="No", value="No")],
        selector="#onsite",
        is_required=True,
    )
    assert solver.solve_question(onsite).target_value == "Yes"

    github = ScreeningQuestion(
        field_id="gh",
        question_text="GitHub URL",
        input_type=InputType.TEXT,
        selector="#gh",
        is_required=True,
    )
    assert solver.solve_question(github).target_value == "https://test.dev"

    country = ScreeningQuestion(
        field_id="cc",
        question_text="Phone country code",
        input_type=InputType.TEXT,
        selector="#cc",
        is_required=True,
    )
    assert solver.solve_question(country).target_value == "United States (+1)"

    gender = ScreeningQuestion(
        field_id="gender",
        question_text="Gender",
        input_type=InputType.SELECT,
        options=[
            OptionItem(label="Male", value="male"),
            OptionItem(label="Female", value="female"),
            OptionItem(label="Decline to self-identify", value="decline"),
        ],
        selector="#gender",
        is_required=True,
    )
    assert solver.solve_question(gender).target_value == "Decline to self-identify"


def test_confidence_discard_threshold():
    solver = ScreeningSolver(profile_path="tests/fixtures/mock_profile.json")
    low_conf = QuestionSolution(action_type="type", target_value="Maybe", confidence=0.72, reasoning="Uncertain")
    high_conf = QuestionSolution(action_type="type", target_value="Yes", confidence=0.95, reasoning="Confident")
    assert solver.should_discard(low_conf, threshold=0.85) is True
    assert solver.should_discard(high_conf, threshold=0.85) is False
