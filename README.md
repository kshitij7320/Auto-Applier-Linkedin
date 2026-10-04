# Autonomous LinkedIn Easy Apply System

An edge-to-edge automated LinkedIn Easy Apply system designed for human-grade stealth, resilience, and precision. It connects directly to an existing, authenticated user Chrome session via Chrome DevTools Protocol (CDP), emulates organic human input kinematics with cubic Bézier curves and log-normal keystroke dispatch, resolves custom employer screening questionnaires using structured LLM outputs, and tracks application state in SQLite to guarantee duplicate suppression.

---

## Key Features

1. **Zero Headless Mode (Authenticated Remote CDP)**:
   - Connects to your running browser instance via `connect_over_cdp("http://localhost:9222")`.
   - Preserves session cookies, 2FA state, canvas fingerprints, and TLS client hello signatures.
2. **Human-Centric Kinematics**:
   - **Cubic Bézier Cursor Movement**: Trajectories calculated with randomized perpendicular control points.
   - **Target Overshooting & Correction**: Mouse movements overshoot element bounding boxes by 4–12px and perform corrective micro-adjustments into element padding.
   - **Log-Normal Keystroke Dispatch**: Native keyboard events (`keyboard.press`) with realistic typing cadences, micro-pauses for punctuation and capital letters. No direct DOM `.value` injection.
3. **Fault-Tolerant Screening Solver**:
   - **Tier 1 (Deterministic Matcher)**: Instant resolution of standard questions (US work authorization, visa sponsorship, years of experience with specific technologies, clearance, expected compensation, phone number, terms checkboxes).
   - **Tier 2 (Gemini Structured Outputs)**: Custom employer screening questions routed to `google-genai` (`gemini-2.5-flash`) enforcing strict Pydantic schemas (`action_type`, `target_value`, `confidence`, `reasoning`).
   - **Confidence Circuit Breaker**: If confidence drops below `0.85` on a required question, the system aborts and cleanly dismisses the modal to prevent inaccurate submissions.
   - **SQLite QA Cache**: Answers are hashed and cached to eliminate redundant API calls.
4. **Concurrency & Rate Limiting**:
   - Single-threaded, serial execution.
   - Hard cap of 20 applications per session.
   - Duplicate suppression: checks `applied_jobs` table before clicking any job card.
   - Three-consecutive-error circuit breaker.

---

## Project Structure

```
Job-Linkedin-Auto-Applier/
├── config/
│   ├── profile.json            # Candidate resume context, work auth, skills, preferences
│   └── settings.json           # Session parameters, search URL, application caps, delays
├── storage/
│   └── tracker.db              # SQLite database (applied_jobs, screening_qa_cache)
├── src/
│   ├── __init__.py
│   ├── db.py                   # SQLite schema manager & duplicate query interface
│   ├── stealth.py              # CDP connector, cubic Bézier trajectories, human typing
│   ├── extractor.py            # DOM element parser into Pydantic QuestionElement models
│   ├── solver.py               # Deterministic profile matcher + Gemini structured output engine
│   ├── modal_navigator.py      # Finite State Machine for modal steps (NEXT, REVIEW, SUBMIT, DISCARD)
│   └── main.py                 # Job search pagination, card loop, circuit breaker orchestration
├── tests/
│   ├── fixtures/
│   │   └── mock_modal.html     # Multi-step mock LinkedIn Easy Apply modal DOM
│   ├── test_db.py              # Database tracker unit tests
│   ├── test_stealth.py         # Bézier curve math & overshoot trajectory tests
│   ├── test_extractor.py       # Modal DOM extraction tests
│   ├── test_solver.py          # Deterministic matching & discard threshold tests
│   ├── test_modal_navigator.py # State machine & form filling integration tests
│   ├── test_main_logic.py      # Duplicate suppression & rate limiter tests
│   └── test_dry_run.py         # End-to-end offline pipeline verification
├── requirements.txt
└── README.md
```

---

## Installation & Setup

### 1. Environment Scaffolding
```bash
# Clone and enter the repository
cd Job-Linkedin-Auto-Applier

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser engines (for offline unit tests)
playwright install chromium
```

### 2. Configure Profile & Settings
1. Edit `config/profile.json` with your real contact information, work authorization status, skill experience years, and compensation preferences.
2. If custom screening question resolution via Gemini is desired, set your Gemini API key:
   ```bash
   export GEMINI_API_KEY="your-gemini-api-key"
   ```
3. Edit `config/settings.json` to customize target search keywords, delays, and daily application limits.

---

## Running Chrome with Remote Debugging

To allow the bot to connect to your live, authenticated LinkedIn session, launch Google Chrome with the remote debugging port enabled:

### On macOS:
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-cdp-profile"
```

### On Linux:
```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-cdp-profile"
```

### On Windows:
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data\CDP"
```

> **Note**: Log into LinkedIn in this Chrome window before starting the script. Once logged in, your session remains persisted.

---

## Running Verification & Dry-Run Tests

The entire system can be tested offline against mock LinkedIn HTML modal structures without touching live servers or requiring active credentials:

```bash
source .venv/bin/activate
pytest tests/ -v
```

All tests run in headless mode against local DOM fixtures, validating:
- Bézier curve trajectory mathematics and endpoint precision.
- DOM parsing across radio buttons, select dropdowns, text/numeric inputs, textareas, and checkboxes.
- Two-tier question resolution and confidence threshold gating (<0.85 = discard).
- SQLite duplicate suppression and application history tracking.

---

## Launching the Autonomous Applier

Once Chrome is running with `--remote-debugging-port=9222` and you are logged into LinkedIn:

```bash
source .venv/bin/activate
python src/main.py
```

### Operational Circuit Breakers
- **Hard Application Cap**: Stops immediately upon reaching 20 submitted applications in the session.
- **Duplicate Suppression**: Queries `storage/tracker.db` prior to clicking any job card. If already applied, the job card is skipped.
- **Stuck / Validation Breaker**: If required fields trigger validation errors or cannot be resolved with >= 0.85 confidence, the modal is discarded and closed to keep the loop moving safely.
- **Consecutive Error Breaker**: If 3 consecutive unexpected exceptions occur, the session terminates to prevent anomalous loops.
