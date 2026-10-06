import json
import os
import re
from typing import Any, Dict, List, Optional

from src.extractor import InputType, OptionItem, ScreeningQuestion

# Questions LinkedIn Easy Apply commonly asks this search. Live captures overwrite options.
QUESTION_CATALOG: List[Dict[str, Any]] = [
    {"question_text": "Email address", "input_type": "select", "options": ["kshitij.mern@gmail.com"], "is_required": True},
    {"question_text": "Phone country code", "input_type": "select", "options": ["India (+91)", "United States (+1)"], "is_required": True},
    {"question_text": "Mobile phone number", "input_type": "text", "is_required": True},
    {"question_text": "First name", "input_type": "text", "is_required": True},
    {"question_text": "Last name", "input_type": "text", "is_required": True},
    {"question_text": "City", "input_type": "text", "is_required": True},
    {"question_text": "Location (city)", "input_type": "text", "is_required": True},
    {"question_text": "LinkedIn profile URL", "input_type": "text", "is_required": False},
    {"question_text": "GitHub URL", "input_type": "text", "is_required": False},
    {"question_text": "Portfolio URL", "input_type": "text", "is_required": False},
    {"question_text": "Website", "input_type": "text", "is_required": False},
    {"question_text": "Are you legally authorized to work in India?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you legally authorized to work in the United States?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Will you now or in the future require sponsorship for employment visa status?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Do you have an active security clearance?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you comfortable working in an onsite setting?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you comfortable working from the office?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you comfortable with hybrid work?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you willing to relocate?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "We must fill this position urgently. Can you start immediately?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Can you join immediately?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "What is your notice period?", "input_type": "select", "options": ["Immediate", "15 days", "30 days", "60 days", "90 days"], "is_required": True},
    {"question_text": "How many years of work experience do you have?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with JavaScript?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with React?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with React.js?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with Node.js?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with Express.js?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with MongoDB?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with Python?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with SQL?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with HTML?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with CSS?", "input_type": "numeric", "is_required": True},
    {"question_text": "How many years of work experience do you have with Git?", "input_type": "numeric", "is_required": True},
    {"question_text": "Expected CTC", "input_type": "numeric", "is_required": True},
    {"question_text": "Current CTC", "input_type": "numeric", "is_required": True},
    {"question_text": "What is your expected salary?", "input_type": "numeric", "is_required": True},
    {"question_text": "Do you have a bachelor's degree?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "What is your highest level of education?", "input_type": "select", "options": ["High school", "Bachelor's degree", "Master's degree", "Doctorate"], "is_required": True},
    {"question_text": "University", "input_type": "text", "is_required": True},
    {"question_text": "College name", "input_type": "text", "is_required": False},
    {"question_text": "Field of study", "input_type": "text", "is_required": False},
    {"question_text": "Graduation year", "input_type": "numeric", "is_required": True},
    {"question_text": "Current job title", "input_type": "text", "is_required": False},
    {"question_text": "Current company", "input_type": "text", "is_required": False},
    {"question_text": "How did you hear about this job?", "input_type": "select", "options": ["LinkedIn", "Company website", "Referral", "Other"], "is_required": True},
    {"question_text": "Are you at least 18 years of age?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you comfortable communicating in English?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Are you willing to undergo a background check?", "input_type": "radio", "options": ["Yes", "No"], "is_required": True},
    {"question_text": "Gender", "input_type": "select", "options": ["Male", "Female", "Decline to self-identify"], "is_required": False},
    {"question_text": "Veteran status", "input_type": "select", "options": ["I am not a protected veteran", "Decline to self-identify"], "is_required": False},
    {"question_text": "Disability status", "input_type": "select", "options": ["No, I do not have a disability", "Yes, I have a disability", "Decline to self-identify"], "is_required": False},
    {"question_text": "Message to the hiring manager", "input_type": "textarea", "is_required": False},
    {"question_text": "Cover letter", "input_type": "textarea", "is_required": False},
    {"question_text": "Mark job as a top choice", "input_type": "checkbox", "is_required": False},
    {"question_text": "I agree to the terms and conditions", "input_type": "checkbox", "is_required": True},
]


def normalize_question(text: str) -> str:
    cleaned = (text or "").replace("*", " ")
    cleaned = re.sub(r"\brequired\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.lower().split())
    words = cleaned.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if words[:half] == words[half:]:
            cleaned = " ".join(words[:half])
    return cleaned


def _question_from_record(key: str, record: Dict[str, Any]) -> ScreeningQuestion:
    input_type = InputType(record.get("input_type") or "text")
    options = [
        OptionItem(label=str(label), value=str(label))
        for label in record.get("options") or []
    ]
    return ScreeningQuestion(
        field_id=f"bank-{normalize_question(key)[:40]}",
        question_text=record.get("question_text") or key,
        input_type=input_type,
        options=options,
        is_required=bool(record.get("is_required")),
        selector="#bank",
    )


class QuestionBank:
    def __init__(self, path: str):
        self.path = path
        self.questions: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        self.questions = {}
        self._index = {}
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            for key, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                label = " ".join(str(entry.get("question_text") or key).split())
                norm = normalize_question(label)
                existing_key = self._index.get(norm)
                if existing_key is None:
                    entry["question_text"] = label
                    self.questions[label] = entry
                    self._index[norm] = label
                    continue
                current = self.questions[existing_key]
                current["options"] = list(dict.fromkeys([*(current.get("options") or []), *(entry.get("options") or [])]))
                current["seen_in_jobs"] = list(dict.fromkeys([*(current.get("seen_in_jobs") or []), *(entry.get("seen_in_jobs") or [])]))
                current["is_required"] = bool(current.get("is_required") or entry.get("is_required"))
                if entry.get("covered") and float(entry.get("confidence") or 0) >= float(current.get("confidence") or 0):
                    current["answer"] = entry.get("answer", "")
                    current["confidence"] = entry.get("confidence", 0)
                    current["covered"] = True
                    current["reasoning"] = entry.get("reasoning", current.get("reasoning", ""))

    def save(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.questions, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def lookup_record(self, question_text: str) -> Optional[Dict[str, Any]]:
        key = self._index.get(normalize_question(question_text))
        if key is None:
            return None
        return self.questions.get(key)

    def record(
        self,
        question: ScreeningQuestion,
        answer: Optional[str],
        confidence: float,
        reasoning: str,
        job_title: str = "",
        covered: Optional[bool] = None,
    ) -> None:
        norm = normalize_question(question.question_text)
        key = self._index.get(norm) or " ".join(question.question_text.split())
        entry = dict(self.questions.get(key) or {})
        entry["question_text"] = " ".join((question.question_text or key).split())
        entry["input_type"] = question.input_type.value
        if question.options:
            merged = list(dict.fromkeys([*(entry.get("options") or []), *[opt.label for opt in question.options]]))
            entry["options"] = merged
        entry["is_required"] = bool(entry.get("is_required") or question.is_required)
        seen = list(entry.get("seen_in_jobs") or [])
        if job_title and job_title not in seen:
            seen.append(job_title)
        entry["seen_in_jobs"] = seen

        is_covered = covered if covered is not None else confidence >= 0.85 and answer is not None and str(answer) != ""
        if question.input_type == InputType.CHECKBOX and confidence >= 0.85:
            is_covered = True
        previous = float(entry.get("confidence") or 0)
        if is_covered and (not entry.get("covered") or confidence >= previous):
            entry["answer"] = "" if answer is None else str(answer)
            entry["confidence"] = confidence
            entry["covered"] = True
            entry["reasoning"] = reasoning
        elif "covered" not in entry:
            entry["answer"] = "" if answer is None else str(answer)
            entry["confidence"] = confidence
            entry["covered"] = False
            entry["reasoning"] = reasoning

        self.questions[key] = entry
        self._index[norm] = key
        self.save()

    def seed(self, solve, refresh: bool = False) -> int:
        """Fill catalog questions. refresh=True updates answers after profile rules change."""
        added = 0
        for item in QUESTION_CATALOG:
            existing = self.lookup_record(item["question_text"])
            if existing and existing.get("covered") and not refresh:
                continue
            question = _question_from_record(item["question_text"], item)
            solution = solve(question)
            before = len(self.questions)
            self.record(
                question,
                solution.target_value,
                solution.confidence,
                solution.reasoning,
                job_title="question catalog",
            )
            if len(self.questions) >= before:
                added += 1
        self.save()
        return added

    def refresh_saved(self, solve) -> None:
        """Recompute answers for questions already on disk."""
        for key, entry in list(self.questions.items()):
            question = _question_from_record(key, entry)
            solution = solve(question)
            self.record(
                question,
                solution.target_value,
                solution.confidence,
                solution.reasoning,
            )

    def uncovered_required(self) -> List[str]:
        missing = []
        for key, entry in self.questions.items():
            if entry.get("is_required") and not entry.get("covered"):
                missing.append(entry.get("question_text") or key)
        return missing
