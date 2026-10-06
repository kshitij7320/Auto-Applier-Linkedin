import re
from typing import Awaitable, Callable, Optional, Set

from playwright.async_api import Locator, Page

JOB_ID_IN_HREF = re.compile(r"/jobs/view/(\d+)")

DETAILS_ROOT = (
    ".jobs-search__job-details--container, "
    ".jobs-search__job-details, "
    ".jobs-details, "
    ".job-view-layout"
)

APPLY_BUTTONS = (
    "#jobs-apply-button-id, "
    ".jobs-s-apply button.jobs-apply-button, "
    ".jobs-s-apply button, "
    ".jobs-apply-button--top-card button, "
    "button.jobs-apply-button, "
    "button[aria-label*='Easy Apply' i]"
)

PAGINATION_NEXT = (
    ".jobs-search-pagination button[aria-label='View next page'], "
    ".jobs-search-pagination li.selected + li button, "
    ".jobs-search-pagination li.active + li button, "
    ".artdeco-pagination li.selected + li button, "
    ".artdeco-pagination li.active + li button, "
    "button[aria-label='View next page']"
)

ClickFn = Callable[[Locator], Awaitable[None]]


async def button_label(button: Locator) -> str:
    try:
        text = await button.inner_text()
    except Exception:
        text = ""
    aria = await button.get_attribute("aria-label") or ""
    return " ".join(f"{text} {aria}".lower().split())


async def read_job_id(card: Locator) -> str:
    for attr in ("data-occludable-job-id", "data-job-id"):
        value = (await card.get_attribute(attr) or "").strip()
        if value:
            return value
    link = card.locator("a[href*='/jobs/view/']").first
    if await link.count() > 0:
        href = await link.get_attribute("href") or ""
        match = JOB_ID_IN_HREF.search(href)
        if match:
            return match.group(1)
    return ""


async def job_title_link(card: Locator) -> Locator:
    link = card.locator(
        "a.job-card-container__link, "
        "a.job-card-list__title--link, "
        "a.job-card-list__title, "
        "a[href*='/jobs/view/']"
    ).first
    if await link.count() > 0:
        return link
    return card


def normalize_job_title(raw: str) -> str:
    text = " ".join((raw or "").replace("\n", " ").split())
    text = re.sub(r"\s+with verification$", "", text, flags=re.IGNORECASE)
    words = text.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if [word.lower() for word in words[:half]] == [word.lower() for word in words[half:]]:
            text = " ".join(words[:half])
    return text


async def read_card_title(card: Locator) -> str:
    """Prefer the longer visible title so 'Java' is not dropped from an aria-label."""
    candidates = []
    el = card.locator(
        ".artdeco-entity-lockup__title, .job-card-list__title, a.job-card-container__link"
    ).first
    if await el.count() > 0:
        parts = [part.strip() for part in (await el.inner_text()).splitlines() if part.strip()]
        if parts:
            candidates.append(normalize_job_title(parts[0]))
    link = card.locator("a[href*='/jobs/view/'], a.job-card-container__link").first
    if await link.count() > 0:
        aria = normalize_job_title(await link.get_attribute("aria-label") or "")
        if aria:
            candidates.append(aria)
    if not candidates:
        return "Unknown Title"
    return max(candidates, key=len)


async def read_card_text(card: Locator) -> str:
    try:
        return " ".join((await card.inner_text()).split())
    except Exception:
        return ""


async def read_details_title(page: Page) -> str:
    heading = page.locator(
        ".jobs-search__job-details h1, .jobs-details h1, "
        ".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title"
    ).first
    if await heading.count() == 0:
        return ""
    return normalize_job_title(await heading.inner_text())


async def card_already_applied(card: Locator) -> bool:
    try:
        text = await card.inner_text()
    except Exception:
        return False
    return any(line.strip().lower() == "applied" for line in text.splitlines())


def _contains_term(title: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if term == "java":
        return re.search(r"(?<![a-z0-9])java(?![a-z0-9])", title) is not None
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789.+#" for char in term):
        return term in title
    if term in {"node", "react", "express", "mongo", "mern", "git", "css", "sql", "jwt", "html"}:
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?:\.?\s*js)?(?![a-z0-9])", title) is not None
    # Keep "jsp" from matching the ".js" + "p" join in "React.jsPostgresql".
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", title) is not None


def title_rejection_reason(
    title: str,
    required_terms: Optional[list] = None,
    excluded_stacks: Optional[list] = None,
    excluded_seniority: Optional[list] = None,
) -> Optional[str]:
    """Return why a title should be skipped, or None when it is a target role."""
    text = " ".join((title or "").lower().split())
    for phrase in excluded_seniority or []:
        if phrase and phrase.lower() in text:
            return "seniority"
    for stack in excluded_stacks or []:
        if _contains_term(text, stack):
            return "stack"
    terms = required_terms or []
    if terms and not any(_contains_term(text, term) for term in terms):
        return "role"
    return None


def title_priority(title: str, priority_skills: Optional[list] = None) -> int:
    """Lower number is applied first. Primary stack titles come before generic ones."""
    text = " ".join((title or "").lower().split())
    skills = [skill.lower() for skill in (priority_skills or ["react.js", "node.js", "express.js", "mongodb"])]
    aliases = skills + ["react", "node", "express", "mongo", "mern"]
    if any(skill in text for skill in aliases):
        return 0
    return 1


def posted_within_hours(age_text: str, max_hours: int = 24) -> Optional[bool]:
    """True when a card is recent, False when it is older, None when the age is missing."""
    text = " ".join((age_text or "").lower().split())
    if not text:
        return None
    if any(token in text for token in ["just now", "moment ago", "today", "minute", "hour"]):
        hour_match = re.search(r"(\d+)\s*hour", text)
        if hour_match and int(hour_match.group(1)) > max_hours:
            return False
        return True
    if any(token in text for token in ["week", "month", "year"]):
        return False
    day_match = re.search(r"(\d+)\s*day", text)
    if day_match:
        return int(day_match.group(1)) <= 1
    return None


async def read_card_posted_text(card: Locator) -> str:
    time_el = card.locator("time").first
    if await time_el.count() > 0:
        return " ".join((await time_el.inner_text()).split())
    try:
        return " ".join((await card.inner_text()).split())
    except Exception:
        return ""


async def read_card_company(card: Locator) -> str:
    el = card.locator(
        ".job-card-container__primary-description, "
        ".artdeco-entity-lockup__subtitle, "
        ".job-card-container__company-name"
    ).first
    if await el.count() == 0:
        return "Unknown Company"
    text = " ".join((await el.inner_text()).split())
    return text or "Unknown Company"


def _details_root(page: Page) -> Locator:
    return page.locator(DETAILS_ROOT).first


async def _visible_apply_buttons(page: Page) -> Locator:
    details = _details_root(page)
    root = details if await details.count() > 0 else page
    return root.locator(APPLY_BUTTONS)


async def find_easy_apply_button(page: Page) -> Optional[Locator]:
    """Return the visible Easy Apply or in-progress Continue button in the details pane."""
    buttons = await _visible_apply_buttons(page)
    count = await buttons.count()
    for i in range(count):
        button = buttons.nth(i)
        try:
            if not await button.is_visible() or await button.is_disabled():
                continue
        except Exception:
            continue
        label = await button_label(button)
        if "easy apply" in label:
            return button
        if label == "continue" or "continue application" in label:
            return button
    return None


async def job_marked_applied(page: Page) -> bool:
    buttons = await _visible_apply_buttons(page)
    count = await buttons.count()
    for i in range(count):
        button = buttons.nth(i)
        try:
            if not await button.is_visible():
                continue
        except Exception:
            continue
        label = await button_label(button)
        if label == "applied" or label.startswith("applied ") or "already applied" in label:
            return True
    try:
        details = _details_root(page)
        if await details.count() > 0:
            text = await details.inner_text()
            if any(line.strip().lower() == "applied" for line in text.splitlines()):
                return True
    except Exception:
        return False
    return False


async def is_auth_wall(page: Page) -> bool:
    url = (page.url or "").lower()
    if "/login" in url or "/uas/login" in url or "authwall" in url:
        return True
    wall = page.locator("div.authwall, form.login__form, input#username, input#session_key")
    if await wall.count() == 0:
        return False
    try:
        return await wall.first.is_visible()
    except Exception:
        return False


async def wait_for_selected_job(page: Page, job_id: str, title: str = "", timeout_ms: int = 8000) -> bool:
    try:
        await page.wait_for_function(
            """([jobId, title]) => {
                const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const url = new URL(window.location.href);
                if (url.searchParams.get('currentJobId') === String(jobId)) return true;
                const details = document.querySelector(
                    '.jobs-search__job-details--container, .jobs-search__job-details, .jobs-details, .job-view-layout, .scaffold-layout__detail, .jobs-details__main-content'
                );
                if (!details) return false;
                if (details.querySelector(`a[href*="/jobs/view/${jobId}"]`)) return true;
                if (details.getAttribute('data-job-id') === String(jobId)) return true;
                const wanted = norm(title).slice(0, 80);
                const heading = details.querySelector(
                    'h1, h2, .job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title'
                );
                if (wanted && heading && norm(heading.innerText).includes(wanted)) return true;
                return !!(wanted && norm(details.innerText).includes(wanted));
            }""",
            arg=[str(job_id), title or ""],
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def open_job_matches(page: Page, job_id: str, title: str) -> bool:
    """Broad check used when the strict details-pane wait misses a layout change."""
    state = await page.evaluate(
        """([jobId, title]) => {
            const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
            const url = new URL(window.location.href);
            const details = document.querySelector(
                '.jobs-search__job-details--container, .jobs-search__job-details, .jobs-details, .job-view-layout, .scaffold-layout__detail, .jobs-details__main-content'
            );
            const heading = details ? details.querySelector('h1, h2') : document.querySelector('h1');
            return {
                currentJobId: url.searchParams.get('currentJobId') || '',
                heading: heading ? norm(heading.innerText) : '',
                detailsText: details ? norm(details.innerText).slice(0, 800) : '',
                jobId: String(jobId),
                title: norm(title).slice(0, 80),
            };
        }""",
        [str(job_id), title or ""],
    )
    current = state.get("currentJobId") or ""
    if current:
        return current == str(job_id)
    wanted = state.get("title") or ""
    if wanted and wanted in (state.get("heading") or ""):
        return True
    if wanted and wanted in (state.get("detailsText") or ""):
        return True
    return False


async def wait_for_easy_apply_modal(page: Page, timeout_ms: int = 8000) -> bool:
    try:
        await page.wait_for_selector(
            ".jobs-easy-apply-modal, "
            ".jobs-easy-apply-content, "
            "[role='dialog'] button[aria-label='Continue to next step'], "
            "[role='dialog'] button[aria-label='Review your application'], "
            "[role='dialog'] button[aria-label='Submit application'], "
            "[role='dialog'] button[aria-label*='Continue applying' i]",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _collect_job_ids(page: Page) -> Set[str]:
    ids = await page.locator("[data-occludable-job-id], [data-job-id]").evaluate_all(
        """els => els.map(el => el.getAttribute('data-occludable-job-id') || el.getAttribute('data-job-id')).filter(Boolean)"""
    )
    return set(ids)


async def load_more_results(page: Page, click: Optional[ClickFn] = None) -> bool:
    """Advance LinkedIn's results list. Ignores unrelated Next buttons elsewhere on the page."""
    before = await _collect_job_ids(page)
    next_btn = page.locator(PAGINATION_NEXT).first
    advanced = False
    if await next_btn.count() > 0:
        try:
            if await next_btn.is_visible():
                if click is not None:
                    await click(next_btn)
                else:
                    await next_btn.click()
                advanced = True
        except Exception:
            advanced = False

    if not advanced:
        try:
            moved = await page.evaluate(
                """() => {
                    const candidates = Array.from(document.querySelectorAll(
                        '.jobs-search-results-list, .scaffold-layout__list'
                    ));
                    const el = candidates.find(node => node.scrollHeight > node.clientHeight + 20) || candidates[0];
                    if (!el) return false;
                    const prev = el.scrollTop;
                    el.scrollTop = prev + Math.max(el.clientHeight * 0.85, 300);
                    return el.scrollTop > prev + 5;
                }"""
            )
        except Exception:
            return False
        if not moved:
            return False

    for _ in range(6):
        await page.wait_for_timeout(400)
        if await _collect_job_ids(page) - before:
            return True
    return False
