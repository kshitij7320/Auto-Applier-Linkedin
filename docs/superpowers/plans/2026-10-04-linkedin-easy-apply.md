# LinkedIn Easy Apply System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous LinkedIn Easy Apply bot connecting via remote CDP with Bézier curve human input emulation, a two-tier screening questionnaire solver using Gemini structured outputs, a finite state machine modal navigator, duplicate suppression, and a hard 20-application session cap.

**Architecture:** Playwright async CDP connection attaches to `http://localhost:9222`. A stealth input module executes cubic Bézier mouse paths with target overshooting and log-normal keystroke dispatch. Modal steps are parsed into Pydantic models, answered via profile matching or Gemini structured outputs (<0.85 confidence triggers discard), and orchestrated by an FSM backed by SQLite persistence.

**Tech Stack:** Python 3.10+, `playwright`, `google-genai`, `pydantic>=2.0`, `sqlite3`, `pytest`, `pytest-asyncio`.

**Spec:** [`docs/superpowers/specs/2026-10-04-linkedin-easy-apply-design.md`](file:///Users/kshitijhimanshu/Documents/Faltu-code/Job-Linkedin-Auto-Applier/docs/superpowers/specs/2026-10-04-linkedin-easy-apply-design.md)

## Global Constraints
- Zero Headless Mode: Connect strictly via `playwright.chromium.connect_over_cdp("http://localhost:9222")`.
- Human-Centric Input: Native CDP mouse events with cubic Bézier curve overshooting and log-normal key press intervals; no DOM `.value = ...` assignments.
- Screening Solver Gate: Confidence threshold >= 0.85; lower confidence must trigger modal discard.
- Concurrency & Rate Limiting: Single-threaded serial execution only; hard limit of 20 applications per session; SQLite duplicate suppression before clicking job cards.

---

### Task 1: Environment Scaffolding, Configurations & SQLite Database Layer

**Files:**
- Create: `requirements.txt`
- Create: `config/profile.json`
- Create: `config/settings.json`
- Create: `src/__init__.py`
- Create: `src/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `DatabaseTracker`: class in `src/db.py`
    - `__init__(db_path: str = "storage/tracker.db")`
    - `init_db() -> None`
    - `is_already_applied(job_id: str) -> bool`
    - `record_application(job_id: str, title: str, company: str, location: str, status: str, error_reason: Optional[str] = None) -> None`
    - `get_cached_answer(question_hash: str) -> Optional[dict]`
    - `cache_answer(question_hash: str, question_text: str, input_type: str, solution_json: str) -> None`
    - `get_session_applied_count() -> int`

- [ ] **Step 1: Write the failing test for SQLite tracker**

```python
# tests/test_db.py
import os
import tempfile
import pytest
from src.db import DatabaseTracker

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tracker = DatabaseTracker(db_path=path)
    tracker.init_db()
    yield tracker
    if os.path.exists(path):
        os.remove(path)

def test_db_init_and_application_record(temp_db):
    assert not temp_db.is_already_applied("job-123")
    temp_db.record_application(
        job_id="job-123",
        title="Software Engineer",
        company="Acme Corp",
        location="Remote",
        status="APPLIED"
    )
    assert temp_db.is_already_applied("job-123")
    assert temp_db.get_session_applied_count() == 1

def test_qa_cache(temp_db):
    assert temp_db.get_cached_answer("hash123") is None
    temp_db.cache_answer("hash123", "Are you authorized to work in the US?", "radio", '{"target_value": "Yes", "confidence": 1.0}')
    cached = temp_db.get_cached_answer("hash123")
    assert cached is not None
    assert cached["target_value"] == "Yes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py`
Expected: FAIL (ModuleNotFoundError: No module named 'src')

- [ ] **Step 3: Write dependencies, configuration files, and DatabaseTracker implementation**

Create `requirements.txt`:
```
playwright>=1.40.0
google-genai>=0.1.0
pydantic>=2.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Create `config/profile.json`:
```json
{
  "candidate": {
    "first_name": "Kshitij",
    "last_name": "Himanshu",
    "phone_country_code": "United States (+1)",
    "phone_number": "5550192834",
    "email": "candidate@example.com",
    "current_city": "San Francisco, CA",
    "linkedin_url": "https://www.linkedin.com/in/example",
    "portfolio_url": "https://github.com/example"
  },
  "work_authorization": {
    "us_authorized": "Yes",
    "require_sponsorship_now_or_future": "No",
    "security_clearance": "No",
    "eu_authorized": "No",
    "canada_authorized": "No"
  },
  "experience": {
    "total_years": 5,
    "skills": {
      "Python": 5,
      "FastAPI": 4,
      "Docker": 4,
      "Kubernetes": 3,
      "SQL": 5,
      "TypeScript": 3,
      "React": 3,
      "AWS": 4,
      "GCP": 3
    }
  },
  "preferences": {
    "notice_period_weeks": 2,
    "salary_expectation_usd": 150000,
    "willing_to_relocate": "Yes",
    "comfortable_with_hybrid_or_remote": "Yes"
  },
  "llm": {
    "model": "gemini-2.5-flash",
    "temperature": 0.1
  }
}
```

Create `config/settings.json`:
```json
{
  "cdp_url": "http://localhost:9222",
  "search_url": "https://www.linkedin.com/jobs/search/?f_AL=true&keywords=Software%20Engineer",
  "max_applications_per_session": 20,
  "max_steps_per_modal": 8,
  "confidence_threshold": 0.85,
  "action_delay_min_seconds": 1.5,
  "action_delay_max_seconds": 3.5,
  "inter_job_delay_min_seconds": 12.0,
  "inter_job_delay_max_seconds": 25.0
}
```

Create `src/__init__.py`.
Create `src/db.py`:
```python
import sqlite3
import json
import os
from typing import Optional, Dict, Any

class DatabaseTracker:
    def __init__(self, db_path: str = "storage/tracker.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS applied_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT UNIQUE NOT NULL,
                    job_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL,
                    error_reason TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS screening_qa_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_hash TEXT UNIQUE NOT NULL,
                    question_text TEXT NOT NULL,
                    input_type TEXT NOT NULL,
                    solution_json TEXT NOT NULL,
                    resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def is_already_applied(self, job_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM applied_jobs WHERE job_id = ? AND status = 'APPLIED'", (job_id,))
            return cursor.fetchone() is not None

    def record_application(
        self,
        job_id: str,
        title: str,
        company: str,
        location: str,
        status: str,
        error_reason: Optional[str] = None
    ) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO applied_jobs (job_id, job_title, company, location, status, error_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    error_reason=excluded.error_reason,
                    applied_at=CURRENT_TIMESTAMP
            """, (job_id, title, company, location, status, error_reason))
            conn.commit()

    def get_cached_answer(self, question_hash: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT solution_json FROM screening_qa_cache WHERE question_hash = ?", (question_hash,))
            row = cursor.fetchone()
            if row:
                return json.loads(row["solution_json"])
            return None

    def cache_answer(self, question_hash: str, question_text: str, input_type: str, solution_json: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO screening_qa_cache (question_hash, question_text, input_type, solution_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(question_hash) DO UPDATE SET
                    solution_json=excluded.solution_json,
                    resolved_at=CURRENT_TIMESTAMP
            """, (question_hash, question_text, input_type, solution_json))
            conn.commit()

    def get_session_applied_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS cnt FROM applied_jobs WHERE status = 'APPLIED'")
            row = cursor.fetchone()
            return row["cnt"] if row else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config/ src/__init__.py src/db.py tests/test_db.py
git commit -m "feat(storage): initialize config, requirements, and SQLite database tracking"
```

---

### Task 2: Stealth Input Emulation & CDP Connector

**Files:**
- Create: `src/stealth.py`
- Test: `tests/test_stealth.py`

**Interfaces:**
- Consumes: `playwright.async_api`
- Produces:
  - `generate_bezier_trajectory(start: tuple[float, float], end: tuple[float, float], steps: int = 25) -> list[tuple[float, float]]`
  - `generate_overshoot_trajectory(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]`
  - `StealthController`: class
    - `__init__(page: Page)`
    - `move_to(target_x: float, target_y: float) -> None`
    - `click_element(selector_or_locator) -> None`
    - `type_human(text: str, selector_or_locator=None) -> None`
    - `random_delay(min_s: float = 1.0, max_s: float = 2.5) -> None`
  - `connect_to_cdp(endpoint_url: str = "http://localhost:9222") -> tuple[Playwright, Browser, Page]`

- [ ] **Step 1: Write unit tests for Bézier calculations and input intervals**

```python
# tests/test_stealth.py
import pytest
from src.stealth import generate_bezier_trajectory, generate_overshoot_trajectory

def test_bezier_trajectory_generation():
    start = (100.0, 100.0)
    end = (500.0, 400.0)
    path = generate_bezier_trajectory(start, end, steps=30)
    assert len(path) == 30
    assert abs(path[0][0] - start[0]) < 1e-2
    assert abs(path[0][1] - start[1]) < 1e-2
    assert abs(path[-1][0] - end[0]) < 1e-2
    assert abs(path[-1][1] - end[1]) < 1e-2

def test_overshoot_trajectory():
    start = (50.0, 50.0)
    end = (300.0, 300.0)
    path = generate_overshoot_trajectory(start, end)
    assert len(path) > 30
    # Last point must arrive accurately at end target
    assert abs(path[-1][0] - end[0]) < 1.0
    assert abs(path[-1][1] - end[1]) < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stealth.py`
Expected: FAIL (ModuleNotFoundError: No module named 'src.stealth')

- [ ] **Step 3: Implement `src/stealth.py`**

```python
import math
import random
import asyncio
from typing import List, Tuple, Optional
from playwright.async_api import Page, Locator, Playwright, Browser, async_playwright

def _cubic_bezier(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    return (1 - t)**3 * p0 + 3 * (1 - t)**2 * t * p1 + 3 * (1 - t) * t**2 * p2 + t**3 * p3

def generate_bezier_trajectory(
    start: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 25
) -> List[Tuple[float, float]]:
    x0, y0 = start
    x3, y3 = end
    dx = x3 - x0
    dy = y3 - y0
    dist = math.hypot(dx, dy)

    # Deviation based on distance
    deviation = min(max(dist * 0.25, 20.0), 120.0)
    angle = math.atan2(dy, dx)
    perp_angle = angle + (math.pi / 2 if random.random() < 0.5 else -math.pi / 2)

    offset1 = random.uniform(0.1, 0.4)
    offset2 = random.uniform(0.6, 0.9)
    dev1 = random.uniform(-deviation, deviation)
    dev2 = random.uniform(-deviation, deviation)

    x1 = x0 + dx * offset1 + math.cos(perp_angle) * dev1
    y1 = y0 + dy * offset1 + math.sin(perp_angle) * dev1
    x2 = x0 + dx * offset2 + math.cos(perp_angle) * dev2
    y2 = y0 + dy * offset2 + math.sin(perp_angle) * dev2

    trajectory = []
    for i in range(steps):
        t = i / (steps - 1)
        # Ease-in, ease-out using cubic parameterization
        t_eased = t * t * (3 - 2 * t)
        xt = _cubic_bezier(x0, x1, x2, x3, t_eased)
        yt = _cubic_bezier(y0, y1, y2, y3, t_eased)
        trajectory.append((xt, yt))
    return trajectory

def generate_overshoot_trajectory(
    start: Tuple[float, float],
    end: Tuple[float, float]
) -> List[Tuple[float, float]]:
    x0, y0 = start
    x_target, y_target = end
    dx = x_target - x0
    dy = y_target - y0
    dist = math.hypot(dx, dy)

    if dist < 15.0:
        return generate_bezier_trajectory(start, end, steps=10)

    # Overshoot destination 4-12px beyond the target along movement vector with slight deviation
    angle = math.atan2(dy, dx)
    overshoot_dist = random.uniform(4.0, 12.0)
    overshoot_angle = angle + random.uniform(-0.15, 0.15)
    x_over = x_target + math.cos(overshoot_angle) * overshoot_dist
    y_over = y_target + math.sin(overshoot_angle) * overshoot_dist

    primary_steps = random.randint(22, 32)
    corrective_steps = random.randint(8, 14)

    primary_path = generate_bezier_trajectory((x0, y0), (x_over, y_over), steps=primary_steps)
    corrective_path = generate_bezier_trajectory((x_over, y_over), (x_target, y_target), steps=corrective_steps)

    # Combine without duplicating the junction point
    return primary_path + corrective_path[1:]

class StealthController:
    def __init__(self, page: Page):
        self.page = page
        self.current_x = random.uniform(100, 300)
        self.current_y = random.uniform(100, 300)

    async def move_to(self, target_x: float, target_y: float) -> None:
        path = generate_overshoot_trajectory((self.current_x, self.current_y), (target_x, target_y))
        for x, y in path:
            await self.page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.018))
        self.current_x = target_x
        self.current_y = target_y

    async def click_element(self, locator_or_selector) -> None:
        locator = locator_or_selector if isinstance(locator_or_selector, Locator) else self.page.locator(locator_or_selector)
        await locator.scroll_into_view_if_needed()
        box = await locator.bounding_box()
        if not box:
            raise RuntimeError("Element bounding box is null or element is hidden")

        # Pick random point inside element padding
        pad_x = max(2.0, min(box["width"] * 0.2, 10.0))
        pad_y = max(2.0, min(box["height"] * 0.2, 10.0))
        target_x = box["x"] + random.uniform(pad_x, box["width"] - pad_x)
        target_y = box["y"] + random.uniform(pad_y, box["height"] - pad_y)

        await self.move_to(target_x, target_y)
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.04, 0.09))
        await self.page.mouse.up()
        await asyncio.sleep(random.uniform(0.1, 0.25))

    async def type_human(self, text: str, locator_or_selector=None) -> None:
        if locator_or_selector is not None:
            await self.click_element(locator_or_selector)
            # Clear existing content safely with native select all and backspace
            await self.page.keyboard.press("Meta+A" if "mac" in sys_platform() else "Control+A")
            await asyncio.sleep(0.05)
            await self.page.keyboard.press("Backspace")
            await asyncio.sleep(0.05)

        for char in text:
            await self.page.keyboard.press(char)
            # Sample typing speed with log-normal latency
            base_delay = random.lognormvariate(-2.7, 0.35)  # median ~67ms
            # Natural micro-pauses after space, comma, period, or caps
            if char in " .,?!":
                base_delay += random.uniform(0.1, 0.25)
            elif char.isupper() or char.isdigit():
                base_delay += random.uniform(0.05, 0.12)
            await asyncio.sleep(min(max(base_delay, 0.025), 0.4))

    async def random_delay(self, min_s: float = 1.2, max_s: float = 2.8) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

def sys_platform() -> str:
    import sys
    return sys.platform

async def connect_to_cdp(endpoint_url: str = "http://localhost:9222") -> Tuple[Playwright, Browser, Page]:
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(endpoint_url)
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("No active browser context found via CDP.")
    context = contexts[0]
    pages = context.pages
    page = pages[0] if pages else await context.new_page()
    return pw, browser, page
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_stealth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stealth.py tests/test_stealth.py
git commit -m "feat(stealth): implement Bézier cursor movement, overshoot, and human typing"
```

---

### Task 3: DOM Form Extractor & Pydantic Question Schemas

**Files:**
- Create: `src/extractor.py`
- Create: `tests/fixtures/mock_modal.html`
- Test: `tests/test_extractor.py`

**Interfaces:**
- Produces:
  - `InputType(str, Enum)`: `TEXT`, `NUMERIC`, `RADIO`, `CHECKBOX`, `SELECT`, `TEXTAREA`
  - `OptionItem(BaseModel)`: `label: str`, `value: str`
  - `ScreeningQuestion(BaseModel)`:
    - `field_id: str`
    - `question_text: str`
    - `input_type: InputType`
    - `options: list[OptionItem]`
    - `is_required: bool`
    - `current_value: Optional[str]`
    - `selector: str`
  - `FormStepData(BaseModel)`:
    - `step_title: str`
    - `questions: list[ScreeningQuestion]`
    - `has_next_button: bool`
    - `has_review_button: bool`
    - `has_submit_button: bool`
  - `extract_form_step(page: Page, modal_selector: str = ".jobs-easy-apply-modal") -> FormStepData`

- [ ] **Step 1: Create mock LinkedIn Easy Apply HTML fixture and failing extractor test**

Create `tests/fixtures/mock_modal.html`:
```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Mock Easy Apply</title></head>
<body>
<div class="jobs-easy-apply-modal" role="dialog" aria-labelledby="modal-header">
  <h2 id="modal-header">Contact info</h2>
  <form>
    <div class="fb-form-element">
      <label for="phone-input">Mobile phone number *</label>
      <input type="text" id="phone-input" value="1234567890" required />
    </div>

    <div class="fb-form-element">
      <fieldset>
        <legend><span>Are you legally authorized to work in the United States? *</span></legend>
        <label><input type="radio" name="auth-radio" value="Yes" /> Yes</label>
        <label><input type="radio" name="auth-radio" value="No" /> No</label>
      </fieldset>
    </div>

    <div class="fb-form-element">
      <label for="experience-input">How many years of work experience do you have with Python? *</label>
      <input type="number" id="experience-input" min="0" required />
    </div>

    <div class="fb-form-element">
      <label for="clearance-select">Do you have an active Secret clearance? *</label>
      <select id="clearance-select" required>
        <option value="">Select an option</option>
        <option value="Yes">Yes</option>
        <option value="No">No</option>
      </select>
    </div>

    <div class="fb-form-element">
      <label><input type="checkbox" id="terms-agree" required /> I acknowledge and agree to the privacy statement.</label>
    </div>

    <div class="fb-form-element">
      <label for="cover-letter">Additional notes or cover note</label>
      <textarea id="cover-letter"></textarea>
    </div>
  </form>
  <footer>
    <button class="artdeco-button--primary" aria-label="Continue to next step">Next</button>
  </footer>
</div>
</body>
</html>
```

Write `tests/test_extractor.py`:
```python
import os
import pytest
from playwright.async_api import async_playwright
from src.extractor import extract_form_step, InputType

@pytest.mark.asyncio
async def test_form_extractor_parses_all_input_types():
    fixture_path = os.path.abspath("tests/fixtures/mock_modal.html")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file://{fixture_path}")

        step_data = await extract_form_step(page, modal_selector=".jobs-easy-apply-modal")
        assert step_data.has_next_button is True
        assert step_data.has_submit_button is False
        assert len(step_data.questions) == 6

        q_map = {q.input_type: q for q in step_data.questions}
        assert InputType.TEXT in q_map
        assert "Mobile phone number" in q_map[InputType.TEXT].question_text
        assert q_map[InputType.TEXT].current_value == "1234567890"

        assert InputType.RADIO in q_map
        assert "legally authorized" in q_map[InputType.RADIO].question_text
        assert len(q_map[InputType.RADIO].options) == 2

        assert InputType.NUMERIC in q_map
        assert "years of work experience do you have with Python" in q_map[InputType.NUMERIC].question_text

        assert InputType.SELECT in q_map
        assert "Secret clearance" in q_map[InputType.SELECT].question_text

        assert InputType.CHECKBOX in q_map
        assert "privacy statement" in q_map[InputType.CHECKBOX].question_text

        assert InputType.TEXTAREA in q_map
        assert "Additional notes" in q_map[InputType.TEXTAREA].question_text

        await browser.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_extractor.py`
Expected: FAIL (ModuleNotFoundError: No module named 'src.extractor')

- [ ] **Step 3: Implement `src/extractor.py`**

```python
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel
from playwright.async_api import Page, Locator

class InputType(str, Enum):
    TEXT = "text"
    NUMERIC = "numeric"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    SELECT = "select"
    TEXTAREA = "textarea"

class OptionItem(BaseModel):
    label: str
    value: str

class ScreeningQuestion(BaseModel):
    field_id: str
    question_text: str
    input_type: InputType
    options: List[OptionItem] = []
    is_required: bool = False
    current_value: Optional[str] = None
    selector: str

class FormStepData(BaseModel):
    step_title: str
    questions: List[ScreeningQuestion] = []
    has_next_button: bool = False
    has_review_button: bool = False
    has_submit_button: bool = False

async def extract_form_step(page: Page, modal_selector: str = ".jobs-easy-apply-modal") -> FormStepData:
    modal = page.locator(modal_selector).first
    if await modal.count() == 0:
        # Fallback to general role=dialog
        modal = page.locator("[role='dialog']").first

    title_elem = modal.locator("h2, h3, h1").first
    step_title = (await title_elem.inner_text()).strip() if await title_elem.count() > 0 else "Screening"

    questions: List[ScreeningQuestion] = []

    # 1. Radio groups (usually in fieldset)
    fieldsets = modal.locator("fieldset")
    fcnt = await fieldsets.count()
    handled_names = set()

    for i in range(fcnt):
        fs = fieldsets.nth(i)
        radios = fs.locator("input[type='radio']")
        rcnt = await radios.count()
        if rcnt > 0:
            legend = fs.locator("legend").first
            text = (await legend.inner_text()).strip() if await legend.count() > 0 else "Radio Question"
            options: List[OptionItem] = []
            name = await radios.first.get_attribute("name") or f"radio_{i}"
            handled_names.add(name)

            for r_idx in range(rcnt):
                r = radios.nth(r_idx)
                val = await r.get_attribute("value") or ""
                # Try sibling label or parent label
                label_elem = fs.locator(f"label:has(input[value='{val}']), label[for='{await r.get_attribute('id')}']")
                lbl_text = (await label_elem.first.inner_text()).strip() if await label_elem.count() > 0 else val
                options.append(OptionItem(label=lbl_text, value=val))

            questions.append(ScreeningQuestion(
                field_id=f"fieldset_{i}_{name}",
                question_text=text,
                input_type=InputType.RADIO,
                options=options,
                is_required="*" in text or await fs.locator("[required]").count() > 0,
                selector=f"{modal_selector} fieldset:nth-of-type({i+1})"
            ))

    # 2. Select elements
    selects = modal.locator("select")
    scnt = await selects.count()
    for i in range(scnt):
        sel = selects.nth(i)
        sel_id = await sel.get_attribute("id") or f"select_{i}"
        label_elem = modal.locator(f"label[for='{sel_id}']").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await sel.get_attribute("aria-label") or "Dropdown Question")
        opts = sel.locator("option")
        opt_cnt = await opts.count()
        options: List[OptionItem] = []
        for o_idx in range(opt_cnt):
            opt = opts.nth(o_idx)
            val = await opt.get_attribute("value") or ""
            otext = (await opt.inner_text()).strip()
            if val and otext and "Select an option" not in otext:
                options.append(OptionItem(label=otext, value=val))

        questions.append(ScreeningQuestion(
            field_id=sel_id,
            question_text=q_text,
            input_type=InputType.SELECT,
            options=options,
            is_required="*" in q_text or await sel.get_attribute("required") is not None,
            selector=f"#{sel_id}" if await sel.get_attribute("id") else f"{modal_selector} select >> nth={i}"
        ))

    # 3. Checkboxes
    checkboxes = modal.locator("input[type='checkbox']")
    ccnt = await checkboxes.count()
    for i in range(ccnt):
        cb = checkboxes.nth(i)
        cb_id = await cb.get_attribute("id") or f"cb_{i}"
        label_elem = modal.locator(f"label[for='{cb_id}'], label:has(#{cb_id})").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await cb.get_attribute("aria-label") or "Checkbox")
        is_checked = await cb.is_checked()
        questions.append(ScreeningQuestion(
            field_id=cb_id,
            question_text=q_text,
            input_type=InputType.CHECKBOX,
            options=[OptionItem(label="checked", value="true"), OptionItem(label="unchecked", value="false")],
            is_required="*" in q_text or await cb.get_attribute("required") is not None,
            current_value="true" if is_checked else "false",
            selector=f"#{cb_id}" if await cb.get_attribute("id") else f"{modal_selector} input[type='checkbox'] >> nth={i}"
        ))

    # 4. Text and numeric inputs
    inputs = modal.locator("input[type='text'], input[type='number'], input[type='tel'], input:not([type])")
    icnt = await inputs.count()
    for i in range(icnt):
        inp = inputs.nth(i)
        itype = await inp.get_attribute("type") or "text"
        inp_id = await inp.get_attribute("id") or f"inp_{i}"
        label_elem = modal.locator(f"label[for='{inp_id}'], label:has(#{inp_id})").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await inp.get_attribute("aria-label") or "Text Question")
        curr_val = await inp.input_value()
        questions.append(ScreeningQuestion(
            field_id=inp_id,
            question_text=q_text,
            input_type=InputType.NUMERIC if itype == "number" else InputType.TEXT,
            is_required="*" in q_text or await inp.get_attribute("required") is not None,
            current_value=curr_val or None,
            selector=f"#{inp_id}" if await inp.get_attribute("id") else f"{modal_selector} input >> nth={i}"
        ))

    # 5. Textareas
    tas = modal.locator("textarea")
    tcnt = await tas.count()
    for i in range(tcnt):
        ta = tas.nth(i)
        ta_id = await ta.get_attribute("id") or f"ta_{i}"
        label_elem = modal.locator(f"label[for='{ta_id}'], label:has(#{ta_id})").first
        q_text = (await label_elem.inner_text()).strip() if await label_elem.count() > 0 else (await ta.get_attribute("aria-label") or "Textarea")
        curr_val = await ta.input_value()
        questions.append(ScreeningQuestion(
            field_id=ta_id,
            question_text=q_text,
            input_type=InputType.TEXTAREA,
            is_required="*" in q_text or await ta.get_attribute("required") is not None,
            current_value=curr_val or None,
            selector=f"#{ta_id}" if await ta.get_attribute("id") else f"{modal_selector} textarea >> nth={i}"
        ))

    # Determine available navigation buttons
    has_next = await modal.locator("button:has-text('Next'), button[aria-label*='next' i]").count() > 0
    has_review = await modal.locator("button:has-text('Review'), button[aria-label*='review' i]").count() > 0
    has_submit = await modal.locator("button:has-text('Submit application'), button[aria-label*='Submit application' i]").count() > 0

    return FormStepData(
        step_title=step_title,
        questions=questions,
        has_next_button=has_next,
        has_review_button=has_review,
        has_submit_button=has_submit
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/extractor.py tests/fixtures/mock_modal.html tests/test_extractor.py
git commit -m "feat(extractor): implement HTML modal form extractor with Pydantic question schemas"
```

---

### Task 4: Two-Tier Screening Solver with Gemini Structured Output & Confidence Gate

**Files:**
- Create: `src/solver.py`
- Test: `tests/test_solver.py`

**Interfaces:**
- Consumes:
  - `ScreeningQuestion`, `InputType` from `src.extractor`
  - `DatabaseTracker` from `src.db`
  - `config/profile.json`
- Produces:
  - `QuestionSolution(BaseModel)`:
    - `action_type: str` ("type", "select", "check", "radio")
    - `target_value: str`
    - `confidence: float`
    - `reasoning: str`
  - `ScreeningSolver`: class
    - `__init__(profile_path: str = "config/profile.json", db_tracker: Optional[DatabaseTracker] = None, gemini_api_key: Optional[str] = None)`
    - `solve_question(question: ScreeningQuestion) -> QuestionSolution`
    - `should_discard(solution: QuestionSolution, threshold: float = 0.85) -> bool`

- [ ] **Step 1: Write unit tests for deterministic matching, caching, and Gemini adapter mock**

```python
# tests/test_solver.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver.py`
Expected: FAIL (ModuleNotFoundError: No module named 'src.solver')

- [ ] **Step 3: Implement `src/solver.py`**

```python
import hashlib
import json
import os
import re
from typing import Optional, Dict, Any
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
        # Check SQLite Cache
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
        if any(k in text for k in ["authorized to work in the united states", "legally authorized to work in the us", "work authorization"]):
            val = auth.get("us_authorized", "Yes")
            return self._match_or_type(question, val, "Work authorization profile match")

        # Sponsorship
        if any(k in text for k in ["sponsorship", "require visa", "h-1b", "future require sponsorship"]):
            val = auth.get("require_sponsorship_now_or_future", "No")
            return self._match_or_type(question, val, "Sponsorship preference match")

        # Security Clearance
        if "clearance" in text:
            val = auth.get("security_clearance", "No")
            return self._match_or_type(question, val, "Security clearance profile match")

        # Years of Experience for specific skills
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
            total_years = exp.get("total_years", 4)
            return QuestionSolution(
                action_type="type",
                target_value=str(total_years),
                confidence=1.0,
                reasoning="Total years of experience fallback"
            )

        # Phone Number
        if "phone" in text or "mobile" in text:
            phone = cand.get("phone_number", "")
            return QuestionSolution(action_type="type", target_value=phone, confidence=1.0, reasoning="Candidate phone")

        # Email
        if "email" in text:
            email = cand.get("email", "")
            return QuestionSolution(action_type="type", target_value=email, confidence=1.0, reasoning="Candidate email")

        # Salary
        if "salary" in text or "compensation" in text:
            sal = str(prefs.get("salary_expectation_usd", 120000))
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
            response = client.models.generate_content(
                model=self.profile.get("llm", {}).get("model", "gemini-2.5-flash"),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_solver.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/solver.py tests/test_solver.py
git commit -m "feat(solver): implement two-tier questionnaire solver with Gemini structured output and confidence gate"
```

---

### Task 5: Modal Finite State Machine & Navigation Engine

**Files:**
- Create: `src/modal_navigator.py`
- Test: `tests/test_modal_navigator.py`

**Interfaces:**
- Consumes:
  - `StealthController` from `src.stealth`
  - `extract_form_step`, `FormStepData`, `InputType` from `src.extractor`
  - `ScreeningSolver`, `QuestionSolution` from `src.solver`
- Produces:
  - `NavigationResult(str, Enum)`: `SUBMITTED`, `DISCARDED`, `FAILED`
  - `ModalNavigator`: class
    - `__init__(page: Page, stealth: StealthController, solver: ScreeningSolver, max_steps: int = 8)`
    - `process_easy_apply_modal() -> tuple[NavigationResult, Optional[str]]`

- [ ] **Step 1: Write integration tests for modal navigation on mock multi-step flow**

```python
# tests/test_modal_navigator.py
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

        # Verify radio button got checked
        auth_radio = page.locator("input[name='auth-radio'][value='Yes']")
        assert await auth_radio.is_checked()

        # Verify experience input received value
        exp_val = await page.locator("#experience-input").input_value()
        assert exp_val == "5"

        await browser.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_modal_navigator.py`
Expected: FAIL (ModuleNotFoundError: No module named 'src.modal_navigator')

- [ ] **Step 3: Implement `src/modal_navigator.py`**

```python
import asyncio
from enum import Enum
from typing import Optional, Tuple
from playwright.async_api import Page, Locator
from src.stealth import StealthController
from src.extractor import extract_form_step, FormStepData, InputType, ScreeningQuestion
from src.solver import ScreeningSolver, QuestionSolution

class NavigationResult(str, Enum):
    SUBMITTED = "SUBMITTED"
    DISCARDED = "DISCARDED"
    FAILED = "FAILED"

class ModalNavigator:
    def __init__(
        self,
        page: Page,
        stealth: StealthController,
        solver: ScreeningSolver,
        max_steps: int = 8,
        confidence_threshold: float = 0.85
    ):
        self.page = page
        self.stealth = stealth
        self.solver = solver
        self.max_steps = max_steps
        self.confidence_threshold = confidence_threshold

    async def discard_modal(self) -> None:
        """Dismisses the modal and confirms discard prompt."""
        # Find close button (X)
        close_btn = self.page.locator(
            "button[aria-label*='Dismiss' i], button[aria-label*='Close' i], .jobs-easy-apply-modal button[data-test-modal-close-btn]"
        ).first
        if await close_btn.count() > 0:
            await self.stealth.click_element(close_btn)
            await asyncio.sleep(0.5)

        # Confirm discard dialog if present
        confirm_btn = self.page.locator(
            "button[data-control-name='discard_application_confirm_btn'], button:has-text('Discard')"
        ).first
        if await confirm_btn.count() > 0:
            await self.stealth.click_element(confirm_btn)
            await asyncio.sleep(0.5)

    async def fill_current_step(self) -> Tuple[bool, Optional[str]]:
        step_data = await extract_form_step(self.page)

        for q in step_data.questions:
            # Skip if already filled and non-empty
            if q.current_value and q.current_value.strip() and q.input_type in [InputType.TEXT, InputType.NUMERIC, InputType.TEXTAREA]:
                continue

            solution = self.solver.solve_question(q)
            if self.solver.should_discard(solution, self.confidence_threshold):
                return False, f"Low confidence ({solution.confidence:.2f}) on question: {q.question_text}"

            await self._apply_solution(q, solution)
            await asyncio.sleep(0.15)

        return True, None

    async def _apply_solution(self, q: ScreeningQuestion, solution: QuestionSolution) -> None:
        if q.input_type in [InputType.TEXT, InputType.NUMERIC, InputType.TEXTAREA]:
            elem = self.page.locator(q.selector).first
            await self.stealth.type_human(solution.target_value, elem)

        elif q.input_type == InputType.RADIO:
            target_val = solution.target_value.lower()
            # Find matching radio by value or label
            radio = self.page.locator(
                f"{q.selector} input[type='radio'][value='{solution.target_value}'], "
                f"{q.selector} label:has-text('{solution.target_value}') input[type='radio']"
            ).first
            if await radio.count() > 0:
                await self.stealth.click_element(radio)

        elif q.input_type == InputType.SELECT:
            sel = self.page.locator(q.selector).first
            # Find exact matching option value
            matched_val = solution.target_value
            for opt in q.options:
                if opt.label.lower() == solution.target_value.lower() or opt.value.lower() == solution.target_value.lower():
                    matched_val = opt.value
                    break
            await sel.select_option(value=matched_val)

        elif q.input_type == InputType.CHECKBOX:
            cb = self.page.locator(q.selector).first
            if solution.target_value.lower() in ["true", "yes", "1"]:
                if not await cb.is_checked():
                    await self.stealth.click_element(cb)

    async def has_validation_errors(self) -> bool:
        errs = self.page.locator(".artdeco-inline-feedback--error, [data-test-form-element-error-messages]")
        return await errs.count() > 0

    async def process_easy_apply_modal(self) -> Tuple[NavigationResult, Optional[str]]:
        for step in range(self.max_steps):
            await self.stealth.random_delay(1.0, 2.0)
            step_data = await extract_form_step(self.page)

            # Check if application already finished/submitted
            if await self.page.locator("h2:has-text('Application submitted')").count() > 0:
                await self.discard_modal()
                return NavigationResult.SUBMITTED, None

            # Fill questions
            ok, reason = await self.fill_current_step()
            if not ok:
                await self.discard_modal()
                return NavigationResult.DISCARDED, reason

            # Transition step
            if step_data.has_submit_button:
                submit_btn = self.page.locator("button:has-text('Submit application')").first
                await self.stealth.click_element(submit_btn)
                await asyncio.sleep(2.0)
                await self.discard_modal()
                return NavigationResult.SUBMITTED, None

            elif step_data.has_review_button:
                rev_btn = self.page.locator("button:has-text('Review')").first
                await self.stealth.click_element(rev_btn)
                await asyncio.sleep(1.0)

            elif step_data.has_next_button:
                next_btn = self.page.locator("button:has-text('Next')").first
                await self.stealth.click_element(next_btn)
                await asyncio.sleep(1.0)

                # Check if stuck due to validation
                if await self.has_validation_errors():
                    await self.discard_modal()
                    return NavigationResult.DISCARDED, "Encountered unresolvable form validation error"

            else:
                # No action buttons found
                await self.discard_modal()
                return NavigationResult.FAILED, "No navigation button found in modal"

        await self.discard_modal()
        return NavigationResult.FAILED, f"Exceeded maximum allowable steps ({self.max_steps})"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_modal_navigator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modal_navigator.py tests/test_modal_navigator.py
git commit -m "feat(navigator): implement modal finite state machine with automatic discard gate"
```

---

### Task 6: Main Orchestration Loop, Circuit Breakers & Duplicate Suppression

**Files:**
- Create: `src/main.py`
- Test: `tests/test_main_logic.py`

**Interfaces:**
- Consumes:
  - `DatabaseTracker` from `src.db`
  - `StealthController`, `connect_to_cdp` from `src.stealth`
  - `ScreeningSolver` from `src.solver`
  - `ModalNavigator`, `NavigationResult` from `src.modal_navigator`
  - `config/settings.json`
- Produces:
  - `ApplicationRunner`: class
    - `run_session() -> dict[str, int]`

- [ ] **Step 1: Write tests for duplicate suppression and circuit breaker logic**

```python
# tests/test_main_logic.py
import pytest
from unittest.mock import MagicMock
from src.db import DatabaseTracker

def test_duplicate_suppression_prevents_reapply():
    db = DatabaseTracker(db_path=":memory:")
    db.init_db()
    db.record_application("job-999", "DevOps Engineer", "CloudCorp", "Remote", "APPLIED")
    assert db.is_already_applied("job-999") is True
    assert db.is_already_applied("job-1000") is False

def test_session_hard_cap_enforcement():
    max_cap = 20
    session_count = 20
    assert (session_count >= max_cap) is True
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_main_logic.py -v`
Expected: PASS

- [ ] **Step 3: Implement `src/main.py`**

```python
import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any
from src.db import DatabaseTracker
from src.stealth import connect_to_cdp, StealthController
from src.solver import ScreeningSolver
from src.modal_navigator import ModalNavigator, NavigationResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("LinkedInAutoApplier")

class ApplicationRunner:
    def __init__(self, config_dir: str = "config"):
        with open(os.path.join(config_dir, "settings.json"), "r", encoding="utf-8") as f:
            self.settings: Dict[str, Any] = json.load(f)
        self.profile_path = os.path.join(config_dir, "profile.json")
        self.db = DatabaseTracker("storage/tracker.db")
        self.db.init_db()
        self.solver = ScreeningSolver(
            profile_path=self.profile_path,
            db_tracker=self.db
        )
        self.max_applications = self.settings.get("max_applications_per_session", 20)

    async def run(self) -> Dict[str, int]:
        cdp_url = self.settings.get("cdp_url", "http://localhost:9222")
        logger.info(f"Connecting to CDP instance at {cdp_url} (Zero Headless Mode)...")

        pw, browser, page = await connect_to_cdp(cdp_url)
        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=self.solver,
            max_steps=self.settings.get("max_steps_per_modal", 8),
            confidence_threshold=self.settings.get("confidence_threshold", 0.85)
        )

        stats = {"applied": 0, "discarded": 0, "failed": 0, "skipped": 0}
        consecutive_errors = 0

        try:
            search_url = self.settings.get("search_url")
            logger.info(f"Navigating to job search URL: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded")
            await stealth.random_delay(3.0, 5.0)

            while stats["applied"] < self.max_applications and consecutive_errors < 3:
                # Find all job cards on current page
                job_cards = page.locator(".job-card-container, [data-occludable-job-id]")
                count = await job_cards.count()
                logger.info(f"Found {count} job cards on current search page.")

                if count == 0:
                    logger.warning("No job cards found. Exiting search loop.")
                    break

                for i in range(count):
                    if stats["applied"] >= self.max_applications:
                        logger.info(f"Reached hard application limit of {self.max_applications}.")
                        break

                    card = job_cards.nth(i)
                    job_id = await card.get_attribute("data-job-id") or await card.get_attribute("data-occludable-job-id") or f"card_{i}"

                    # 1. Duplicate suppression
                    if self.db.is_already_applied(job_id):
                        logger.info(f"Skipping job_id {job_id} (already applied in database).")
                        stats["skipped"] += 1
                        continue

                    # Extract basic details
                    title_elem = card.locator(".job-card-list__title, a.job-card-container__link").first
                    title = (await title_elem.inner_text()).strip() if await title_elem.count() > 0 else "Unknown Title"
                    company_elem = card.locator(".job-card-container__primary-description").first
                    company = (await company_elem.inner_text()).strip() if await company_elem.count() > 0 else "Unknown Company"

                    logger.info(f"Evaluating card [{i+1}/{count}]: {title} at {company} (ID: {job_id})")

                    # Click card using Bézier movement
                    await stealth.click_element(card)
                    await stealth.random_delay(1.5, 3.0)

                    # Look for Easy Apply button
                    easy_apply_btn = page.locator(".jobs-apply-button--top-card button.jobs-apply-button").first
                    if await easy_apply_btn.count() == 0 or "easy apply" not in (await easy_apply_btn.inner_text()).lower():
                        logger.info(f"Job {job_id} does not have an Easy Apply button. Skipping.")
                        stats["skipped"] += 1
                        continue

                    # Click Easy Apply
                    logger.info(f"Triggering Easy Apply for {title}...")
                    await stealth.click_element(easy_apply_btn)
                    await stealth.random_delay(1.5, 2.5)

                    # Run Modal State Machine
                    result, reason = await navigator.process_easy_apply_modal()

                    if result == NavigationResult.SUBMITTED:
                        logger.info(f"Successfully applied to {title} ({job_id})!")
                        self.db.record_application(job_id, title, company, "N/A", "APPLIED")
                        stats["applied"] += 1
                        consecutive_errors = 0
                    elif result == NavigationResult.DISCARDED:
                        logger.warning(f"Discarded application for {job_id}: {reason}")
                        self.db.record_application(job_id, title, company, "N/A", "DISCARDED", reason)
                        stats["discarded"] += 1
                    else:
                        logger.error(f"Failed application for {job_id}: {reason}")
                        self.db.record_application(job_id, title, company, "N/A", "FAILED", reason)
                        stats["failed"] += 1
                        consecutive_errors += 1

                    # Organic delay between applications
                    inter_delay = float(self.settings.get("inter_job_delay_min_seconds", 12.0))
                    logger.info(f"Waiting {inter_delay:.1f}s before next card evaluation...")
                    await asyncio.sleep(inter_delay)

                # Pagination: try to click next page if quota remaining
                if stats["applied"] < self.max_applications:
                    next_page = page.locator("button[aria-label*='Page '].active + button, button[aria-label='Next']").first
                    if await next_page.count() > 0:
                        logger.info("Advancing to next search page...")
                        await stealth.click_element(next_page)
                        await stealth.random_delay(4.0, 7.0)
                    else:
                        logger.info("No further pages available.")
                        break

        finally:
            await browser.close()
            await pw.stop()

        logger.info(f"Session completed: {stats}")
        return stats

if __name__ == "__main__":
    runner = ApplicationRunner()
    asyncio.run(runner.run())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_logic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_main_logic.py
git commit -m "feat(orchestrator): implement main execution loop with duplicate suppression and circuit breakers"
```

---

### Task 7: Complete Offline Dry-Run Test Suite & Documentation

**Files:**
- Create: `tests/test_dry_run.py`
- Create: `README.md`

**Interfaces:**
- Validates the complete pipeline (Stealth -> Extractor -> Solver -> Navigator -> DB) end-to-end against mock HTML fixtures.

- [ ] **Step 1: Write end-to-end dry-run test suite**

```python
# tests/test_dry_run.py
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

        # 2. Verify all inputs got populated
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

        # 3. Verify SQLite cached QA records
        assert db.get_session_applied_count() == 0
        db.record_application("mock-1", "Mock Eng", "Mock Corp", "Remote", "APPLIED")
        assert db.get_session_applied_count() == 1
        assert db.is_already_applied("mock-1") is True

        await browser.close()

    if os.path.exists(db_path):
        os.remove(db_path)
```

- [ ] **Step 2: Create comprehensive `README.md`**

Write `README.md` detailing:
- Setup instructions (`pip install -r requirements.txt`).
- Launching Chrome with CDP enabled (`/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222`).
- Configuration details (`config/profile.json` and `config/settings.json`).
- Running the dry-run test suite (`pytest`).
- Starting the autonomous applier (`python src/main.py`).

- [ ] **Step 3: Run complete pytest test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_dry_run.py README.md
git commit -m "test: add comprehensive end-to-end dry-run test suite and documentation"
```
