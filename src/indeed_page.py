import re
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import Locator, Page

from src.job_page import posted_within_hours, title_rejection_reason
from src.naukri_page import experience_is_fresher

INDEED_CARDS = "div.job_seen_beacon"
YEAR_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)\s*years?", re.IGNORECASE)
YEARS_MENTION = re.compile(r"(\d+)\s*\+?\s*years?", re.IGNORECASE)


def indeed_search_url(keyword: str) -> str:
    """Entry-level Indeed India search for one skill, posted in the last day."""
    return "https://in.indeed.com/jobs?" + urlencode({
        "q": keyword,
        "fromage": "1",
        "sc": "0kf:explvl(ENTRY_LEVEL);",
    })


def is_placeholder_job_id(job_id: str) -> bool:
    """Indeed inserts hidden cards whose ids are sequential hex, not real jobs."""
    value = (job_id or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16}", value):
        return False
    alphabet = "0123456789abcdef"
    run = 1
    longest = 1
    for left, right in zip(value, value[1:]):
        diff = (alphabet.index(right) - alphabet.index(left)) % 16
        if diff in {1, 15}:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    if longest >= 8:
        return True
    numbers = [int(value[index:index + 2], 16) for index in range(0, 16, 2)]
    diffs = [numbers[index + 1] - numbers[index] for index in range(len(numbers) - 1)]
    return max(diffs.count(item) for item in set(diffs)) >= 5


def indeed_apply_is_blocked(text: str) -> bool:
    cleaned = " ".join((text or "").lower().split())
    return "unable to process the application" in cleaned or "something went wrong" in cleaned


def indeed_job_key(job_id: str) -> str:
    value = str(job_id).strip()
    if value.startswith("indeed:"):
        return value
    return f"indeed:{value}"


def indeed_view_url(job_id: str) -> str:
    return f"https://in.indeed.com/viewjob?jk={job_id}"


def card_is_indeed_apply(card_text: str) -> bool:
    return "easily apply" in " ".join((card_text or "").lower().split())


def indeed_experience_is_fresher(text: str) -> Optional[bool]:
    """True for 0 years. False when the card asks for 1 year or more."""
    parsed = experience_is_fresher(text)
    if parsed is not None:
        return parsed
    ranged = YEAR_RANGE.search(text or "")
    if ranged:
        return int(ranged.group(1)) <= 0
    match = YEARS_MENTION.search(text or "")
    if match:
        return int(match.group(1)) == 0
    return None


def indeed_posted_recently(age_text: str, max_hours: int = 24) -> Optional[bool]:
    text = " ".join((age_text or "").lower().split())
    if not text:
        return None
    if "starts in" in text:
        return None
    if any(token in text for token in ["just posted", "just now", "today", "few hour", "minute", "hour"]):
        return True
    return posted_within_hours(text, max_hours)


def card_should_skip(
    title: str,
    card_text: str,
    required_terms: list,
    excluded_stacks: list,
    excluded_seniority: list,
) -> Optional[str]:
    return title_rejection_reason(
        title,
        required_terms=required_terms,
        excluded_stacks=excluded_stacks,
        excluded_seniority=excluded_seniority,
    ) or title_rejection_reason(
        card_text,
        required_terms=[],
        excluded_stacks=excluded_stacks,
        excluded_seniority=[],
    )


def is_on_site_apply_label(label: str) -> bool:
    text = " ".join((label or "").lower().split())
    if "company site" in text or "company website" in text:
        return False
    return text in {"apply now", "apply"}


async def read_indeed_job_id(card: Locator) -> str:
    link = card.locator("a.jcs-JobTitle[data-jk], h2.jobTitle a[data-jk]").first
    if await link.count() == 0:
        return ""
    job_id = (await link.get_attribute("data-jk") or "").strip()
    if is_placeholder_job_id(job_id):
        return ""
    return job_id


async def indeed_card_is_visible(card: Locator) -> bool:
    try:
        box = await card.bounding_box()
    except Exception:
        return False
    return bool(box and box.get("width", 0) > 2 and box.get("height", 0) > 2)


async def read_indeed_title(card: Locator) -> str:
    link = card.locator("h2.jobTitle a, a.jcs-JobTitle, a[data-jk]").first
    if await link.count() == 0:
        return "Unknown Title"
    return " ".join((await link.inner_text()).split()) or "Unknown Title"


async def read_indeed_company(card: Locator) -> str:
    named = card.locator("[data-testid='company-name']").first
    if await named.count() > 0:
        text = " ".join((await named.inner_text()).split())
        if text:
            return text
    lines = [line.strip() for line in (await card.inner_text()).splitlines() if line.strip()]
    for line in lines[1:]:
        if line.lower() != "easily apply":
            return line
    return "Unknown Company"


async def read_indeed_posted_text(card: Locator) -> str:
    try:
        text = " ".join((await card.inner_text()).split())
    except Exception:
        return ""
    lowered = text.lower()
    for token in ("just posted", "just now", "today", "few hours ago", "few hour ago"):
        if token in lowered:
            return token
    match = re.search(r"\d+\s+(?:minute|hour|day|week|month)s?\s+ago", lowered)
    return match.group(0) if match else ""


async def read_indeed_card_text(card: Locator) -> str:
    try:
        return " ".join((await card.inner_text()).split())
    except Exception:
        return ""


async def find_indeed_apply_link(page: Page) -> Optional[Locator]:
    """Indeed Apply only. Company-site links are left alone."""
    controls = page.locator("a, button")
    count = await controls.count()
    for i in range(count):
        control = controls.nth(i)
        try:
            if not await control.is_visible():
                continue
        except Exception:
            continue
        label = " ".join(((await control.inner_text()) or "").lower().split())
        if not label:
            label = " ".join(((await control.get_attribute("aria-label")) or "").lower().split())
        href = (await control.get_attribute("href") or "").lower()
        if "company site" in label or "company website" in label:
            continue
        if not is_on_site_apply_label(label):
            continue
        if href and "smartapply.indeed.com" not in href and label != "apply now":
            continue
        if label == "apply now" or "smartapply.indeed.com" in href:
            return control
    return None


async def has_indeed_company_site(page: Page) -> bool:
    control = page.get_by_text("Apply on company site", exact=False)
    count = await control.count()
    for i in range(count):
        try:
            if await control.nth(i).is_visible():
                return True
        except Exception:
            continue
    return False


async def indeed_already_applied(page: Page) -> bool:
    try:
        return bool(await page.evaluate("""() => {
            const text = document.body ? document.body.innerText : "";
            return /you have applied|already applied|application has been submitted/i.test(text);
        }"""))
    except Exception:
        return False


async def find_indeed_continue_button(page: Page) -> Optional[Locator]:
    groups = (
        page.locator("button[data-testid='continue-button']"),
        page.get_by_role("button", name=re.compile(r"^submit your application$", re.I)),
        page.get_by_role("button", name=re.compile(r"^continue$", re.I)),
    )
    for buttons in groups:
        count = await buttons.count()
        for i in range(count):
            button = buttons.nth(i)
            try:
                if await button.is_visible():
                    return button
            except Exception:
                continue
    return None


async def indeed_apply_blocked(page: Page) -> bool:
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return False
    return indeed_apply_is_blocked(text)


async def wait_for_indeed_apply_step(page: Page, timeout_ms: int = 20000) -> bool:
    try:
        await page.wait_for_function(
            """() => {
                const text = document.body ? document.body.innerText : "";
                if (/unable to process the application|something went wrong/i.test(text)) return true;
                if (/application has been submitted|your application was submitted|application submitted/i.test(text)) return true;
                if (/preparing review/i.test(text)) return false;
                if (document.querySelector("[data-testid='loading-indicator']")) return false;
                const buttons = Array.from(document.querySelectorAll("button"));
                return buttons.some((button) => {
                    const label = (button.innerText || "").replace(/\\s+/g, " ").trim();
                    const ready = label === "Continue" || label === "Submit your application"
                        || button.getAttribute("data-testid") === "continue-button";
                    return ready && (button.offsetWidth || button.offsetHeight);
                });
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def indeed_apply_succeeded(page: Page) -> bool:
    try:
        return bool(await page.evaluate("""() => {
            const text = document.body ? document.body.innerText : "";
            return /application has been submitted/i.test(text)
                || /your application was submitted/i.test(text)
                || /application submitted/i.test(text)
                || /you have applied/i.test(text);
        }"""))
    except Exception:
        return False


async def dismiss_indeed_cookies(page: Page) -> None:
    for name in ("Reject All", "Accept All Cookies"):
        button = page.get_by_role("button", name=name, exact=True)
        try:
            if await button.count() > 0 and await button.first.is_visible():
                await button.first.click(timeout=3000)
                return
        except Exception:
            continue


async def load_more_indeed_results(page: Page) -> bool:
    cards = page.locator(INDEED_CARDS)
    before = set()
    for i in range(await cards.count()):
        job_id = await read_indeed_job_id(cards.nth(i))
        if job_id:
            before.add(job_id)
    href = await page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll("a"));
        const next = links.find((link) => {
            const label = (link.innerText || link.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim();
            return (label === "Next" || label === "Next Page") && link.href;
        });
        return next ? next.href : "";
    }""")
    if not href:
        return False
    try:
        await page.goto(href, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        return False
    for _ in range(8):
        await page.wait_for_timeout(400)
        cards = page.locator(INDEED_CARDS)
        found = set()
        for i in range(await cards.count()):
            job_id = await read_indeed_job_id(cards.nth(i))
            if job_id:
                found.add(job_id)
        if found and not found <= before:
            return True
    return False
