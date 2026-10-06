import hashlib
import json
import os
import re
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()
from pydantic import BaseModel, Field
from src.extractor import ScreeningQuestion, InputType
from src.db import DatabaseTracker
from src.question_bank import QuestionBank

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
        gemini_api_key: Optional[str] = None,
        question_bank_path: Optional[str] = None,
    ):
        with open(profile_path, "r", encoding="utf-8") as f:
            self.profile = json.load(f)
        self.db_tracker = db_tracker
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
        self.question_bank = QuestionBank(question_bank_path) if question_bank_path else None
        self.current_job_title = ""

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
            self._remember(question, deterministic, q_hash)
            return deterministic

        banked = self._resolve_bank(question)
        if banked is not None:
            self._remember(question, banked, q_hash)
            return banked

        # Tier 2: LLM Structured Output with Gemini
        llm_solution = self._resolve_gemini(question)
        self._remember(question, llm_solution, q_hash)
        return llm_solution

    def _remember(self, question: ScreeningQuestion, solution: QuestionSolution, q_hash: str) -> None:
        if self.db_tracker and solution.confidence >= 0.85:
            self.db_tracker.cache_answer(
                q_hash,
                question.question_text,
                question.input_type.value,
                solution.model_dump_json(),
            )
        if self.question_bank is not None:
            self.question_bank.record(
                question,
                solution.target_value,
                solution.confidence,
                solution.reasoning,
                job_title=self.current_job_title,
            )

    def preview_answer(self, question: ScreeningQuestion) -> QuestionSolution:
        """Profile or saved-bank answer, without calling the LLM or writing the bank."""
        solved = self._resolve_deterministic(question) or self._resolve_bank(question)
        if solved is not None:
            return solved
        return QuestionSolution(
            action_type="type",
            target_value="",
            confidence=0.0,
            reasoning="No saved answer",
        )

    def _resolve_bank(self, question: ScreeningQuestion) -> Optional[QuestionSolution]:
        if self.question_bank is None:
            return None
        entry = self.question_bank.lookup_record(question.question_text)
        if not entry or not entry.get("covered"):
            return None
        answer = entry.get("answer")
        if answer is None or (answer == "" and question.input_type != InputType.CHECKBOX):
            return None
        action = {
            InputType.RADIO: "radio",
            InputType.SELECT: "select",
            InputType.CHECKBOX: "check",
        }.get(question.input_type, "type")
        return QuestionSolution(
            action_type=action,
            target_value=str(answer),
            confidence=float(entry.get("confidence") or 0.9),
            reasoning=entry.get("reasoning") or "Saved question bank",
        )

    def _resolve_deterministic(self, question: ScreeningQuestion) -> Optional[QuestionSolution]:
        text = question.question_text.lower()
        cand = self.profile.get("candidate", {})
        auth = self.profile.get("work_authorization", {})
        exp = self.profile.get("experience", {})
        prefs = self.profile.get("preferences", {})

        # Work Authorization (India vs US)
        if "india" in text and any(k in text for k in ["authorized", "authorization", "legally"]):
            val = auth.get("india_authorized", "Yes")
            return self._match_or_type(question, val, "India work authorization match")
        if any(k in text for k in ["united states", "in the us", "u.s."]) and any(k in text for k in ["authorized", "authorization", "legally"]):
            val = auth.get("us_authorized", "No")
            return self._match_or_type(question, val, "US work authorization match")
        if any(k in text for k in ["work authorization", "authorized to work", "legally authorized"]):
            val = auth.get("india_authorized", auth.get("us_authorized", "Yes"))
            return self._match_or_type(question, val, "General work authorization profile match")

        # Sponsorship
        if any(k in text for k in ["sponsorship", "require visa", "h-1b", "require sponsorship"]):
            val = auth.get("require_sponsorship_now_or_future", "No")
            return self._match_or_type(question, val, "Sponsorship preference match")

        # Security Clearance
        if "clearance" in text:
            val = auth.get("security_clearance", "No")
            return self._match_or_type(question, val, "Security clearance profile match")

        # Driver's License
        if any(k in text for k in ["driver's license", "driver license", "driving license", "valid license"]):
            val = prefs.get("drivers_license", "Yes")
            return self._match_or_type(question, val, "Driver's license match")

        # Background Check / Drug Screening
        if any(k in text for k in ["background check", "drug test", "drug screen"]):
            val = "Yes"
            return self._match_or_type(question, val, "Background check match")

        # Commute
        if any(k in text for k in ["commute", "travel to", "relocate", "relocation"]):
            val = prefs.get("willing_to_relocate", "Yes")
            return self._match_or_type(question, val, "Commute / Relocation match")

        # Years of Experience. Primary skills are matched before shorter aliases.
        if "experience" in text and ("years" in text or question.input_type == InputType.NUMERIC):
            skills = exp.get("skills", {})
            primary = {name.lower() for name in exp.get("primary_skills", [])}
            ordered = sorted(
                skills.items(),
                key=lambda item: (0 if item[0].lower() in primary else 1, -len(item[0])),
            )
            matched_years = None
            matched_skill = None
            for skill, years in ordered:
                if skill.lower() in text:
                    matched_years = years
                    matched_skill = skill
                    break
            if matched_years is None:
                matched_years = exp.get("total_years", 0)
                matched_skill = "total experience"
            return self._fresher_years_answer(
                question,
                matched_years,
                f"Fresher experience match for {matched_skill}",
            )

        # Phone country code, whether it is a <select> or a typeahead
        if "country code" in text or ("phone" in text and "country" in text):
            code = cand.get("phone_country_code", "India (+91)")
            return self._match_or_type(question, code, "Candidate phone country code")

        # Phone Number (Text / Numeric input)
        if ("phone" in text or "mobile" in text) and question.input_type in [InputType.TEXT, InputType.NUMERIC]:
            phone = cand.get("phone_number", "")
            return QuestionSolution(action_type="type", target_value=phone, confidence=1.0, reasoning="Candidate phone")

        # City / Location. Bare "address" also matches "email address", so keep this specific.
        if "email" not in text and any(k in text for k in ["city", "current location", "reside", "home address", "street address", "where do you live"]):
            city = cand.get("current_city", "Noida, Uttar Pradesh, India")
            return QuestionSolution(action_type="type", target_value=city, confidence=1.0, reasoning="Candidate city match")

        # Work from Office / Onsite
        if any(k in text for k in ["work form office", "work from office", "from the office", "wfo", "on-site", "onsite", "office in"]):
            val = prefs.get("work_from_office", "Yes")
            return self._match_or_type(question, val, "Work from office match")

        # Notice Period
        if "notice" in text:
            if question.options:
                for opt in question.options:
                    label = opt.label.strip().lower()
                    if (
                        "immediate" in label
                        or "less than 1" in label
                        or "0-15" in label
                        or label in {"0", "0 days", "15 days", "15"}
                    ):
                        return self._match_or_type(question, opt.label or opt.value, "Notice period option match")
            val = str(prefs.get("notice_period_days", prefs.get("notice_period_weeks", 0)))
            return self._match_or_type(question, val, "Notice period preference")

        # Name
        if "first name" in text:
            return QuestionSolution(action_type="type", target_value=cand.get("first_name", ""), confidence=1.0, reasoning="Candidate first name")
        if "last name" in text:
            return QuestionSolution(action_type="type", target_value=cand.get("last_name", ""), confidence=1.0, reasoning="Candidate last name")

        # Profile links
        if "github" in text:
            return QuestionSolution(
                action_type="type",
                target_value=cand.get("github_url") or cand.get("portfolio_url", ""),
                confidence=1.0,
                reasoning="Candidate GitHub or portfolio URL",
            )
        if "linkedin" in text:
            return QuestionSolution(action_type="type", target_value=cand.get("linkedin_url", ""), confidence=1.0, reasoning="Candidate LinkedIn URL")
        if any(k in text for k in ["portfolio", "personal website", "website url"]) or text.strip() == "website":
            return QuestionSolution(action_type="type", target_value=cand.get("portfolio_url", ""), confidence=1.0, reasoning="Candidate portfolio URL")

        # Email
        if "email" in text:
            email = cand.get("email", "")
            if question.input_type == InputType.SELECT:
                return QuestionSolution(action_type="select", target_value=email or "PRIMARY", confidence=1.0, reasoning="Candidate email")
            return QuestionSolution(action_type="type", target_value=email, confidence=1.0, reasoning="Candidate email")

        # Current salary is 0 for a fresher. Expected salary stays the profile expectation.
        if any(k in text for k in ["current ctc", "current salary", "current compensation", "current package", "present ctc", "present salary", "last drawn", "existing ctc"]):
            current = str(prefs.get("current_salary_inr", 0))
            return self._match_or_type(question, current, "Current salary is 0")
        if any(k in text for k in ["salary", "compensation", "ctc", "package", "expected ctc"]):
            return self._expected_salary_answer(question, prefs)

        # Urgent start / Start immediately
        if any(k in text for k in ["start immediately", "fill this position urgently", "join immediately", "immediate joiner"]):
            val = prefs.get("start_immediately", "Yes")
            return self._match_or_type(question, val, "Immediate start preference")

        edu = self.profile.get("education", {})
        roles = exp.get("roles") or []
        current_role = next(
            (role for role in roles if str(role.get("end_date", "")).lower() in {"present", "current"}),
            roles[0] if roles else {},
        )
        if any(k in text for k in ["current job title", "current title", "job title"]):
            title = current_role.get("title", "")
            if title:
                return QuestionSolution(action_type="type", target_value=title, confidence=1.0, reasoning="Current role title")
        if any(k in text for k in ["current company", "current employer", "present company"]):
            company = current_role.get("company", "")
            if company:
                return QuestionSolution(action_type="type", target_value=company, confidence=1.0, reasoning="Current employer")
        if any(k in text for k in ["university", "college", "institution", "school name"]):
            school = edu.get("institution", "")
            if school:
                return QuestionSolution(action_type="type", target_value=school, confidence=1.0, reasoning="Education institution")
        if any(k in text for k in ["field of study", "major", "discipline"]):
            field = edu.get("field_of_study", "")
            if field:
                return QuestionSolution(action_type="type", target_value=field, confidence=1.0, reasoning="Field of study")
        if any(k in text for k in ["graduation year", "year of graduation", "when did you graduate"]):
            year = str(edu.get("end_year", "") or "")
            if year:
                return self._match_or_type(question, year, "Graduation year from profile")

        # Degree / Education
        if any(k in text for k in ["bachelor", "degree", "education", "undergraduate", "graduat"]):
            labels = [opt.label.strip().lower() for opt in question.options]
            if "yes" in labels and "no" in labels:
                return self._match_or_type(question, edu.get("completed", "Yes") or "Yes", "Degree completion")
            degree_name = edu.get("degree", "Bachelor's degree")
            if question.options:
                for opt in question.options:
                    label = opt.label.lower()
                    if any(token in label for token in ["bachelor", "b.tech", "btech", "undergraduate", degree_name.lower()]):
                        return self._match_or_type(question, opt.label, "Highest education from profile")
            if question.input_type in [InputType.TEXT, InputType.TEXTAREA, InputType.SELECT]:
                return self._match_or_type(question, degree_name, "Degree name from profile")
            return self._match_or_type(question, edu.get("completed", "Yes") or "Yes", "Bachelor degree completed match")

        if any(k in text for k in ["at least 18", "over 18", "18 years of age", "legal working age"]):
            return self._match_or_type(question, "Yes", "Legal working age")

        if "hybrid" in text:
            return self._match_or_type(question, prefs.get("comfortable_with_hybrid_or_remote", "Yes"), "Hybrid work preference")

        if "english" in text and any(k in text for k in ["fluent", "proficien", "language", "speak"]):
            return self._match_or_type(question, "Yes", "English proficiency")

        if any(k in text for k in ["how did you hear", "where did you hear", "how did you find", "source of application"]):
            if question.options:
                for opt in question.options:
                    if "linkedin" in opt.label.lower():
                        return self._match_or_type(question, opt.label, "Heard about the role on LinkedIn")
            return self._match_or_type(question, "LinkedIn", "Heard about the role on LinkedIn")

        # Resume picker: choose the uploaded file instead of a Yes/No guess
        if question.input_type == InputType.RADIO and question.options:
            labels = [o.label.strip() for o in question.options]
            looks_like_resume = "resume" in text or "curriculum" in text or any(
                label.lower().endswith((".pdf", ".doc", ".docx")) for label in labels
            )
            if looks_like_resume:
                choice = question.options[0]
                return QuestionSolution(
                    action_type="radio",
                    target_value=choice.label or choice.value,
                    confidence=1.0,
                    reasoning="Select the first saved resume",
                )

        # Voluntary self-identification: decline rather than invent a demographic
        if question.options and any(k in text for k in ["gender", "race", "ethnicity", "veteran", "disability", "hispanic", "latino", "sexual orientation"]):
            for opt in question.options:
                label = opt.label.strip().lower()
                if any(k in label for k in ["decline", "prefer not", "don't wish", "do not wish", "choose not"]):
                    return self._match_or_type(question, opt.label, "Decline voluntary self-identification")

        # Top choice / Consent / Checkbox
        if question.input_type == InputType.CHECKBOX:
            if "top choice" in text:
                return QuestionSolution(
                    action_type="check",
                    target_value="false",
                    confidence=1.0,
                    reasoning="Leave top choice unchecked so LinkedIn does not require an extra message",
                )
            if any(k in text for k in ["agree", "acknowledge", "consent", "privacy", "terms"]):
                return QuestionSolution(action_type="check", target_value="true", confidence=1.0, reasoning="Consent agreement checkbox")
            if "follow" in text and question.is_required:
                return QuestionSolution(action_type="check", target_value="true", confidence=1.0, reasoning="Required follow checkbox")
            if "follow" in text:
                return QuestionSolution(action_type="check", target_value="false", confidence=1.0, reasoning="Leave optional follow unchecked")

        # Cover note / Message / Textarea
        if question.input_type == InputType.TEXTAREA or any(k in text for k in ["message", "cover note", "cover letter", "additional information", "note to", "why should we hire you", "about yourself"]):
            note = "Hi, I am Kshitij Himanshu, a Software Development Engineer with hands-on experience in full-stack web development using React.js, Node.js, Express.js, MongoDB, and Redis. I am an immediate joiner eager to contribute to your team."
            return QuestionSolution(action_type="type", target_value=note, confidence=1.0, reasoning="Candidate tailored message")

        # Radio Yes/No intelligent matching
        if question.input_type == InputType.RADIO and question.options:
            lbls = [o.label.strip().lower() for o in question.options]
            if "yes" in lbls and "no" in lbls:
                if any(b in text for b in ["felony", "criminal", "crime", "convicted", "terminate", "fired", "sponsorship", "visa required"]):
                    return QuestionSolution(action_type="radio", target_value="No", confidence=0.92, reasoning="Negative risk screening default")
                return QuestionSolution(action_type="radio", target_value="Yes", confidence=0.92, reasoning="Positive screening qualification default")

        # Numeric field fallback (default 0 for fresher)
        if question.input_type == InputType.NUMERIC:
            return QuestionSolution(action_type="type", target_value="0", confidence=0.88, reasoning="Numeric fresher default")

        return None

    def _expected_salary_answer(self, question: ScreeningQuestion, prefs: Dict[str, Any]) -> QuestionSolution:
        low = int(prefs.get("salary_expectation_min_inr", prefs.get("salary_expectation_inr", 360000)))
        high = int(prefs.get("salary_expectation_max_inr", low))
        reasoning = f"Expected salary is {low} to {high}"
        if question.input_type in [InputType.TEXT, InputType.TEXTAREA] and high != low:
            return QuestionSolution(action_type="type", target_value=f"{low} to {high}", confidence=1.0, reasoning=reasoning)
        if question.input_type == InputType.SELECT and question.options:
            chosen = self._salary_band_option(question, low, high)
            if chosen:
                return self._match_or_type(question, chosen, reasoning)
        return self._match_or_type(question, str(low), reasoning)

    def _salary_band_option(self, question: ScreeningQuestion, low: int, high: int) -> Optional[str]:
        best_label = None
        best_overlap = -1
        for opt in question.options:
            numbers = []
            for raw in re.findall(r"\d[\d,]*", opt.label):
                value = int(raw.replace(",", ""))
                numbers.append(value * 100000 if value < 100 else value)
            if not numbers:
                continue
            band_low, band_high = min(numbers), max(numbers)
            overlap = min(high, band_high) - max(low, band_low)
            if overlap >= 0 and overlap > best_overlap:
                best_overlap = overlap
                best_label = opt.label
        return best_label

    def _fresher_years_answer(self, question: ScreeningQuestion, years, reasoning: str) -> QuestionSolution:
        years_value = int(years) if str(years).isdigit() else 0
        if years_value == 0 and question.options:
            for opt in question.options:
                label = opt.label.strip().lower()
                if any(token in label for token in [
                    "fresher", "fresh graduate", "no experience", "less than 1",
                    "0 year", "0-1", "0 - 1", "< 1", "<1", "entry level", "none",
                ]):
                    return self._match_or_type(question, opt.label, reasoning)
        return self._match_or_type(question, str(years_value), reasoning)

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
            model_name = self.profile.get("llm", {}).get("model", "gemini-3.8-flash")
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
