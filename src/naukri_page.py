import re
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import Locator, Page

from src.job_page import normalize_job_title, posted_within_hours, title_rejection_reason

NAUKRI_CARD = "article.jobTuple, .srp-jobtuple-wrapper, .cust-job-tuple, div[data-job-id].jobTuple"
JOB_ID_IN_URL = re.compile(r"-(\d{6,})(?:\?|$)")
EXPERIENCE_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)\s*yr", re.IGNORECASE)


def naukri_search_url(keyword: str, experience: str = "0-1", job_age_days: int = 1) -> str:
    """One skill per search so Naukri keeps the fresher and last-day filters."""
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    return f"https://www.naukri.com/{slug}-jobs?" + urlencode({
        "k": keyword,
        "experience": experience,
        "jobAge": str(job_age_days),
    })


def is_walk_in(title: str) -> bool:
    text = " ".join((title or "").lower().split())
    return "walk-in" in text or "walk in" in text


def naukri_job_key(job_id: str) -> str:
    value = str(job_id).strip()
    if value.startswith("naukri:"):
        return value
    return f"naukri:{value}"


def experience_is_fresher(text: str) -> Optional[bool]:
    """True for 0 years or an explicit fresher label. False when the minimum is 1 or more."""
    cleaned = " ".join((text or "").lower().split())
    if not cleaned:
        return None
    if "fresher" in cleaned:
        return True
    match = EXPERIENCE_RANGE.search(cleaned)
    if match:
        return int(match.group(1)) <= 0
    plus = re.search(r"(\d+)\s*\+\s*yr", cleaned)
    if plus:
        return int(plus.group(1)) == 0
    return None


async def read_naukri_job_id(card: Locator) -> str:
    for attr in ("data-job-id", "data-jobid"):
        value = (await card.get_attribute(attr) or "").strip()
        if value:
            return value
    link = card.locator("a[href*='naukri.com'], a.title, a").first
    if await link.count() > 0:
        href = await link.get_attribute("href") or ""
        match = JOB_ID_IN_URL.search(href)
        if match:
            return match.group(1)
    return ""


async def read_naukri_title(card: Locator) -> str:
    el = card.locator("a.title, .row1 a, .jobTupleHeader a, a[href*='job-listings']").first
    if await el.count() == 0:
        return "Unknown Title"
    aria = normalize_job_title(await el.get_attribute("aria-label") or "")
    visible = normalize_job_title(await el.inner_text())
    return max([aria, visible], key=len) or "Unknown Title"


async def read_naukri_company(card: Locator) -> str:
    el = card.locator(".comp-name, .companyInfo a, .companyInfo").first
    if await el.count() == 0:
        return "Unknown Company"
    return " ".join((await el.inner_text()).split()) or "Unknown Company"


async def read_naukri_posted_text(card: Locator) -> str:
    el = card.locator(".job-post-day, .job-post-day span, span.type").first
    if await el.count() > 0:
        return " ".join((await el.inner_text()).split())
    try:
        return " ".join((await card.inner_text()).split())
    except Exception:
        return ""


async def read_naukri_experience(card: Locator) -> str:
    el = card.locator(".exp-wrap, .experience, .expwdth").first
    if await el.count() == 0:
        return ""
    return " ".join((await el.inner_text()).split())


def naukri_posted_recently(age_text: str, max_hours: int = 24) -> Optional[bool]:
    text = " ".join((age_text or "").lower().split())
    # Internship cards put the start window here, not the day the job was posted.
    if "starts in" in text:
        return None
    if "few hours" in text or "few hour" in text:
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
    return text in {"apply", "apply now", "quick apply"}


async def find_naukri_apply_button(page: Page) -> Optional[Locator]:
    """On-site Apply only. Company-site and walk-in buttons are left alone."""
    groups = (
        page.locator(
            "#apply-button, button.apply-button, a.apply-button, "
            "[class*='apply-button'], [class*='applyButton']"
        ),
        page.get_by_role("button", name=re.compile(r"^(apply|apply now|quick apply)$", re.I)),
    )
    for buttons in groups:
        count = await buttons.count()
        for i in range(count):
            button = buttons.nth(i)
            try:
                if not await button.is_visible():
                    continue
            except Exception:
                continue
            label = " ".join(((await button.inner_text()) or "").lower().split())
            if not label:
                label = " ".join(((await button.get_attribute("aria-label")) or "").lower().split())
            if is_on_site_apply_label(label):
                return button
    return None


async def load_more_naukri_results(page: Page) -> bool:
    """Open Naukri's Next page when this result list has more jobs."""
    cards = page.locator(".srp-jobtuple-wrapper")
    before = set()
    for i in range(await cards.count()):
        job_id = await read_naukri_job_id(cards.nth(i))
        if job_id:
            before.add(job_id)
    href = await page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll("a"));
        const next = links.find((link) => {
            const label = (link.innerText || "").replace(/\\s+/g, " ").trim();
            return label === "Next" && link.href;
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
        cards = page.locator(".srp-jobtuple-wrapper")
        found = set()
        for i in range(await cards.count()):
            job_id = await read_naukri_job_id(cards.nth(i))
            if job_id:
                found.add(job_id)
        if found and not found <= before:
            return True
    return False


async def has_company_site_apply(page: Page) -> bool:
    button = page.locator("#company-site-button, button.company-site-button")
    count = await button.count()
    for i in range(count):
        try:
            if await button.nth(i).is_visible():
                return True
        except Exception:
            continue
    return False


async def naukri_already_applied(page: Page) -> bool:
    try:
        return bool(await page.evaluate("""() => {
            const text = document.body ? document.body.innerText : "";
            if (/you have successfully applied/i.test(text) || /application sent/i.test(text)) return true;
            const buttons = Array.from(document.querySelectorAll("#apply-button, button.apply-button, a.apply-button"));
            return buttons.some((button) => (button.innerText || "").trim().toLowerCase() === "applied");
        }"""))
    except Exception:
        return False


async def naukri_apply_succeeded(page: Page) -> bool:
    try:
        return bool(await page.evaluate("""() => {
            const text = document.body ? document.body.innerText : "";
            return /successfully applied/i.test(text)
                || /you have applied/i.test(text)
                || /application has been sent/i.test(text)
                || /application sent/i.test(text);
        }"""))
    except Exception:
        return False
