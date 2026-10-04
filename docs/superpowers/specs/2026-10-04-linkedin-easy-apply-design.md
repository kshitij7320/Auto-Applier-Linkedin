# Architecture Design Specification: Edge-to-Edge Automated LinkedIn Easy Apply System

- **Date**: 2026-10-04
- **Status**: Approved
- **Target Platform**: macOS / Python 3.10+
- **Execution Mode**: Remote CDP (`http://localhost:9222`)

---

## 1. System Overview & Directives

The objective is to design, generate, and verify an autonomous, production-grade LinkedIn Easy Apply bot that connects to a user's running Chrome instance via Chrome DevTools Protocol (CDP), resolves custom employer screening questionnaires using structured LLM schemas, and persists application state without triggering anti-bot heuristics.

### Strict Directives
1. **Zero Headless Mode**: Must connect strictly via `playwright.chromium.connect_over_cdp("http://localhost:9222")` to reuse active session cookies, fingerprint, canvas, and TLS characteristics.
2. **Human-Centric Emulation**:
   - Algorithmic cubic Bézier mouse movement with randomized control points and target overshooting/correction.
   - Keystroke entry via native CDP input dispatch with log-normal intervals (no DOM value assignments).
3. **Screening Questionnaire Solver**:
   - Extract form inputs into normalized Pydantic schemas.
   - Tier 1: Deterministic resolution from `config/profile.json`.
   - Tier 2: `google-genai` structured output (`action_type`, `target_value`, `confidence`, `reasoning`).
   - Strict confidence threshold: if confidence < 0.85, trigger modal discard.
4. **Rate Limiting & Duplicate Suppression**:
   - Single-threaded serial execution.
   - Hard limit: 20 applications per session.
   - SQLite check (`applied_jobs`) before opening any job card.

---

## 2. Architecture & File Layout

```
Job-Linkedin-Auto-Applier/
├── config/
│   ├── profile.json            # Candidate details, default answers, work auth
│   └── settings.json           # Session limits, search URLs, delay parameters
├── storage/
│   └── tracker.db              # SQLite persistence (applied_jobs, screening_qa_cache)
├── src/
│   ├── __init__.py
│   ├── db.py                   # SQLite schema manager & query interface
│   ├── stealth.py              # CDP connector, Bézier mouse curve generator, typing emulator
│   ├── extractor.py            # DOM element parser into Pydantic QuestionElement models
│   ├── solver.py               # Deterministic profile matcher + Gemini structured output engine
│   ├── modal_navigator.py      # Finite State Machine for modal steps (NEXT, REVIEW, SUBMIT, DISCARD)
│   └── main.py                 # Job search pagination, card loop, circuit breaker orchestration
├── tests/
│   ├── fixtures/
│   │   └── mock_modal.html     # Multi-step mock LinkedIn Easy Apply modal DOM
│   ├── __init__.py
│   └── test_dry_run.py         # Pytest offline test suite for extractor, solver, and navigation
├── requirements.txt
└── README.md
```

---

## 3. Component Specifications

### 3.1 Stealth & Input Emulation (`src/stealth.py`)
- **Connection**: Connects to `http://localhost:9222` via Playwright's `connect_over_cdp`. Reuses the active browser context and tab.
- **Cubic Bézier Curve Trajectory**:
  - Start point $P_0 = (x_0, y_0)$, Target point $P_3 = (x_3, y_3)$.
  - Control points $P_1$ and $P_2$ computed with perpendicular randomized jitter proportional to Euclidean distance.
  - Overshoot calculation: picks an overshoot destination beyond $P_3$ ($3-12$px), dispatches the primary curve, pauses briefly ($20-60$ms), and executes a subtle corrective Bézier curve into the element's interior bounding box.
  - Native mouse dispatch: dispatches sequence of `page.mouse.move(x, y)` events.
- **Keystroke Emulation**:
  - Uses `page.keyboard.press` for each character with delay sampled from a log-normal distribution ($\mu \approx 65\text{ms}, \sigma \approx 25\text{ms}$).
  - Adds simulated thinking pauses before capital letters, digits, or punctuation.
- **Action Latencies**:
  - Inter-action delays ($1.5\text{s} - 3.8\text{s}$) modeled with Gaussian noise.

### 3.2 Form Extractor (`src/extractor.py`)
- Analyzes DOM subtrees within the Easy Apply modal (`.jobs-easy-apply-modal` or dialog container).
- Normalizes form controls into Pydantic model:
  - `field_id`: unique DOM selector or id
  - `question_text`: extracted from `<label>`, `<legend>`, or `aria-label`
  - `input_type`: `text`, `numeric`, `radio`, `checkbox`, `select`, `textarea`
  - `options`: list of selectable option texts/values
  - `is_required`: boolean flag
  - `current_value`: prefilled string or selection state

### 3.3 Screening Solver (`src/solver.py`)
- **Tier 1 (Deterministic Matcher)**:
  - Normalizes question strings against regex/keyword maps in `config/profile.json` (e.g. "authorized to work in", "require sponsorship", "years of experience", "salary expectation", "security clearance").
  - Returns `QuestionSolution(action_type=..., target_value=..., confidence=1.0, reasoning="Deterministic profile match")`.
- **Tier 2 (Gemini Structured Engine)**:
  - Invokes `google-genai` model (`gemini-2.5-flash`) with structured schema:
    ```python
    class QuestionSolution(BaseModel):
        action_type: Literal["type", "select", "check", "radio"]
        target_value: str
        confidence: float
        reasoning: str
    ```
  - Context includes candidate resume summary, skills, previous jobs, education, and preferences from `config/profile.json`.
- **Confidence Gate**:
  - If `confidence < 0.85`, triggers `abort_step=True`.
- **QA Cache**:
  - Successfully answered questions are cached by normalized question text in `storage/tracker.db`.

### 3.4 Modal State Machine (`src/modal_navigator.py`)
- **Finite States**:
  - `DETECT`: Confirm modal presence and identify active view.
  - `FILL`: Extract fields, compute answers, dispatch Bézier movements and keystrokes.
  - `VALIDATE`: Verify no unhandled inline errors (`.artdeco-inline-feedback--error`).
  - `TRANSITION`: Detect available action button:
    - "Next" -> advance to next step.
    - "Review" -> advance to review step.
    - "Submit application" -> submit and await success modal.
    - "Discard" / "Cancel" -> close modal if low confidence or validation failure.
- **Timeout Protection**:
  - Maximum 8 steps per application; aborts if looping between identical steps.

### 3.5 Database & Tracking (`src/db.py`)
- Tables:
  - `applied_jobs`: `job_id`, `job_title`, `company`, `location`, `applied_at`, `status` (`APPLIED`, `DISCARDED`, `FAILED`), `error_reason`.
  - `screening_qa_cache`: `question_hash`, `question_text`, `input_type`, `solution_json`, `resolved_at`.
- Provides idempotent helper `is_already_applied(job_id) -> bool`.

### 3.6 Orchestrator (`src/main.py`)
- Orchestrates:
  1. CDP connection check.
  2. Database initialization.
  3. Search page navigation (e.g. LinkedIn job search with `f_AL=true` for Easy Apply).
  4. Pagination and job card scanning.
  5. Duplicate check before card selection.
  6. Opening Easy Apply modal and delegating to `ModalNavigator`.
  7. Enforcing rate limits (hard cap of 20 applications) and circuit breakers.

---

## 4. Verification & Testing (`tests/test_dry_run.py`)
- Standalone offline test suite using Playwright against `tests/fixtures/mock_modal.html`.
- Validates:
  1. Bézier path generation and coordinate bounds.
  2. Form input extraction across all input types (text, numeric, select, radio, checkbox, textarea).
  3. Profile matching and Gemini fallback structured output parsing.
  4. FSM transitions: Contact Info -> Work Auth -> Additional Questions -> Review -> Submit.
  5. Low confidence dismissal circuit breaker.
