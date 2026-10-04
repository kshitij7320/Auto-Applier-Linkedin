import hashlib
import json
import os
import re
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from src.extractor import ScreeningQuestion, InputType
from src.db import DatabaseTracker

class QuestionSolution(BaseModel):
    action_type: str = Field(description="Action to take: 'type', 'select', 'check', 'radio'")
    target_value: str = Field(description="Value to fill or option to select")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(description="Explanation of the deduction")

class ScreeningSolver:
    def __init__(
        self,
        profile_path: str = "config/profile.json",
        db_tracker: Optional[DatabaseTracker] = None,
        gemini_api_key: Optional[str] = None
    ):
        with open(profile_path, "r", encoding="utf-8") as f:
            self.profile = json.load(f)
        self.db_tracker = db_tracker
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")

    def _hash_question(self, question: ScreeningQuestion) -> str:
        raw = f"{question.question_text.lower().strip()}_{question.input_type.value}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def solve_question(self, question: ScreeningQuestion) -> QuestionSolution:
        q_hash = self._hash_question(question)
        if self.db_tracker:
            cached = self.db_tracker.get_cached_answer(q_hash)
            if cached:
                return QuestionSolution(**cached)

        # Tier 1: Deterministic Match
        deterministic = self._resolve_deterministic(question)
        if deterministic is not None:
            if self.db_tracker:
                self.db_tracker.cache_answer(
                    q_hash,
                    question.question_text,
                    question.input_type.value,
                    deterministic.model_dump_json()
                )
            return deterministic

        # Tier 2: LLM Structured Output with Gemini
        llm_solution = self._resolve_gemini(question)
        if self.db_tracker and llm_solution.confidence >= 0.85:
            self.db_tracker.cache_answer(
                q_hash,
                question.question_text,
                question.input_type.value,
                llm_solution.model_dump_json()
            )
        return llm_solution

    def _resolve_deterministic(self, question: ScreeningQuestion) -> Optional[QuestionSolution]:
        text = question.question_text.lower()
        cand = self.profile.get("candidate", {})
        auth = self.profile.get("work_authorization", {})
        exp = self.profile.get("experience", {})
        prefs = self.profile.get("preferences", {})

        # Work Authorization (US)
        if any(k in text for k in ["authorized to work in the united states", "legally authorized to work in the us", "work authorization", "authorized to work"]):
            val = auth.get("us_authorized", "Yes")
            return self._match_or_type(question, val, "Work authorization profile match")

        # Sponsorship
        if any(k in text for k in ["sponsorship", "require visa", "h-1b", "require sponsorship"]):
            val = auth.get("require_sponsorship_now_or_future", "No")
            return self._match_or_type(question, val, "Sponsorship preference match")

        # Security Clearance
        if "clearance" in text:
            val = auth.get("security_clearance", "No")
            return self._match_or_type(question, val, "Security clearance profile match")

        # Years of Experience for specific skills or general
        if "experience" in text and ("years" in text or question.input_type == InputType.NUMERIC):
            skills = exp.get("skills", {})
            for skill, years in skills.items():
                if skill.lower() in text:
                    return QuestionSolution(
                        action_type="type",
                        target_value=str(years),
                        confidence=1.0,
                        reasoning=f"Exact skill experience match for {skill}"
                    )
            # Default to total years of experience
            total_years = exp.get("total_years", 5)
            return QuestionSolution(
                action_type="type",
                target_value=str(total_years),
                confidence=1.0,
                reasoning="Total years of experience fallback"
            )

        # Phone Country Code (Select dropdown)
        if question.input_type == InputType.SELECT and any(k in text for k in ["country code", "phone", "mobile"]):
            code = cand.get("phone_country_code", "United States (+1)")
            return QuestionSolution(action_type="select", target_value=code, confidence=1.0, reasoning="Candidate phone country code")

        # Phone Number (Text / Numeric input)
        if ("phone" in text or "mobile" in text) and question.input_type in [InputType.TEXT, InputType.NUMERIC]:
            phone = cand.get("phone_number", "")
            return QuestionSolution(action_type="type", target_value=phone, confidence=1.0, reasoning="Candidate phone")

        # Email
        if "email" in text:
            email = cand.get("email", "")
            return QuestionSolution(action_type="type", target_value=email, confidence=1.0, reasoning="Candidate email")

        # Salary / Compensation
        if "salary" in text or "compensation" in text:
            sal = str(prefs.get("salary_expectation_usd", 150000))
            return QuestionSolution(action_type="type", target_value=sal, confidence=1.0, reasoning="Salary expectation")

        # Checkbox terms / privacy / acknowledge
        if question.input_type == InputType.CHECKBOX and any(k in text for k in ["agree", "acknowledge", "consent", "privacy", "terms"]):
            return QuestionSolution(action_type="check", target_value="true", confidence=1.0, reasoning="Consent agreement checkbox")

        return None

    def _match_or_type(self, question: ScreeningQuestion, desired_value: str, reasoning: str) -> QuestionSolution:
        if question.input_type == InputType.RADIO:
            return QuestionSolution(action_type="radio", target_value=desired_value, confidence=1.0, reasoning=reasoning)
        elif question.input_type == InputType.SELECT:
            return QuestionSolution(action_type="select", target_value=desired_value, confidence=1.0, reasoning=reasoning)
        return QuestionSolution(action_type="type", target_value=desired_value, confidence=1.0, reasoning=reasoning)

    def _resolve_gemini(self, question: ScreeningQuestion) -> QuestionSolution:
        if not self.gemini_api_key:
            return QuestionSolution(
                action_type="type",
                target_value="",
                confidence=0.5,
                reasoning="No GEMINI_API_KEY provided; cannot resolve custom question"
            )

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.gemini_api_key)
            prompt = f"""
You are an autonomous screening question solver for a job candidate.
Given the candidate profile context and the question, deduce the exact, truthful answer and confidence score.

Candidate Profile Context:
{json.dumps(self.profile, indent=2)}

Question:
{question.question_text}
Input Type: {question.input_type.value}
Available Options: {[opt.label for opt in question.options] if question.options else 'Free text'}

Provide your structured answer strictly matching the schema.
"""
            model_name = self.profile.get("llm", {}).get("model", "gemini-2.5-flash")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=QuestionSolution,
                    temperature=0.1
                )
            )
            data = json.loads(response.text)
            return QuestionSolution(**data)
        except Exception as e:
            return QuestionSolution(
                action_type="type",
                target_value="",
                confidence=0.0,
                reasoning=f"LLM resolution failed: {str(e)}"
            )

    def should_discard(self, solution: QuestionSolution, threshold: float = 0.85) -> bool:
        return solution.confidence < threshold
