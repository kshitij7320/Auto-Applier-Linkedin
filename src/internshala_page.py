import re
from typing import Optional

from playwright.async_api import Locator, Page

from src.job_page import posted_within_hours, title_rejection_reason

INTERNSHALA_CARDS = ".individual_internship"
JOB_ID_IN_CARD = re.compile(r"individual_internship_(\d+)")
YEARS_REQUIRED = re.compile(r"(\d+)\s*years?", re.IGNORECASE)


def internshala_keyword_slug(keyword: str) -> str:
    text = keyword.lower().replace(".js", "").replace(".", " ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def internshala_search_urls(keyword: str) -> list:
    """Internship and fresher-job searches for one skill."""
    slug = internshala_keyword_slug(keyword)
    return [
        f"https://internshala.com/internships/keywords-{slug}/",
        f"https://internshala.com/jobs/keywords-{slug}/",
    ]


def internshala_job_key(job_id: str) -> str:
    value = str(job_id).strip()
    if value.startswith("internshala:"):
        return value
    return f"internshala:{value}"


def card_is_easy_apply(class_name: str) -> bool:
    return "easy_apply" in (class_name or "")


def internship_experience_is_fresher(text: str) -> Optional[bool]:
    """True for no experience. False when the listing asks for 1 year or more."""
    cleaned = " ".join((text or "").lower().split())
    if not cleaned:
        return None
    if "no experience" in cleaned or "fresher" in cleaned:
        return True
    match = YEARS_REQUIRED.search(cleaned)
    if match:
        return int(match.group(1)) == 0
    return None


def internshala_posted_recently(age_text: str, max_hours: int = 24) -> Optional[bool]:
    text = " ".join((age_text or "").lower().split())
    if not text:
        return None
    if any(token in text for token in ("just now", "few hour", "today", "hour", "minute")):
        return True
    return posted_within_hours(text, max_hours)


def internship_should_skip(
    title: str,
    skills: str,
    required_terms: list,
    excluded_stacks: list,
    excluded_seniority: list,
) -> Optional[str]:
    """A React or MERN title stays. A generic title must show that skill and no other stack."""
    title_reason = title_rejection_reason(
        title,
        required_terms=required_terms,
        excluded_stacks=excluded_stacks,
        excluded_seniority=excluded_seniority,
    )
    if title_reason in {"seniority", "stack"} or title_reason is None:
        return title_reason
    return title_rejection_reason(
        skills,
        required_terms=required_terms,
        excluded_stacks=excluded_stacks,
        excluded_seniority=[],
    )


async def read_internshala_job_id(card: Locator) -> str:
    card_id = (await card.get_attribute("id") or "").strip()
    match = JOB_ID_IN_CARD.search(card_id)
    return match.group(1) if match else ""


async def read_internshala_title(card: Locator) -> str:
    title = card.locator("h3.job-internship-name, h2.job-internship-name, a.job-title-href").first
    if await title.count() == 0:
        return "Unknown Title"
    return " ".join((await title.inner_text()).split()) or "Unknown Title"


async def read_internshala_company(card: Locator) -> str:
    company = card.locator(".company_name, .company-name").first
    if await company.count() == 0:
        return "Unknown Company"
    text = " ".join((await company.inner_text()).split())
    return text.replace("Actively hiring", "").strip() or "Unknown Company"


async def read_internshala_skills(card: Locator) -> str:
    skills = card.locator(".job_skill")
    names = []
    for i in range(await skills.count()):
        text = " ".join((await skills.nth(i).inner_text()).split())
        if text:
            names.append(text)
    return " ".join(names)


async def read_internshala_posted_text(card: Locator) -> str:
    posted = card.locator(".status-success, .color-labels").first
    if await posted.count() == 0:
        return ""
    return " ".join((await posted.inner_text()).split())


async def read_internshala_card_text(card: Locator) -> str:
    try:
        return " ".join((await card.inner_text()).split())
    except Exception:
        return ""


async def internshala_card_is_visible(card: Locator) -> bool:
    try:
        box = await card.bounding_box()
    except Exception:
        return False
    return bool(box and box.get("width", 0) > 2 and box.get("height", 0) > 2)


async def find_internshala_apply_button(page: Page) -> Optional[Locator]:
    buttons = page.locator("#easy_apply_button, #top_easy_apply_button")
    count = await buttons.count()
    for i in range(count):
        button = buttons.nth(i)
        try:
            if not await button.is_visible():
                continue
        except Exception:
            continue
        label = " ".join(((await button.inner_text()) or "").lower().split())
        if label in {"apply now", "apply"}:
            return button
    return None


async def internshala_already_applied(page: Page) -> bool:
    try:
        return bool(await page.evaluate("""() => {
            const text = document.body ? document.body.innerText : "";
            if (/application submitted/i.test(text) && /track status/i.test(text)) return true;
            const buttons = Array.from(document.querySelectorAll("#easy_apply_button, #top_easy_apply_button, button"));
            return buttons.some((button) => /already applied|^applied$/i.test((button.innerText || "").trim()));
        }"""))
    except Exception:
        return False


async def internshala_apply_succeeded(page: Page) -> bool:
    try:
        return bool(await page.evaluate("""() => {
            const text = document.body ? document.body.innerText : "";
            return /application submitted/i.test(text);
        }"""))
    except Exception:
        return False


async def load_more_internshala_results(page: Page) -> bool:
    cards = page.locator(INTERNSHALA_CARDS)
    before = set()
    for i in range(await cards.count()):
        job_id = await read_internshala_job_id(cards.nth(i))
        if job_id:
            before.add(job_id)
    href = await page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll("a"));
        const next = links.find((link) => {
            const label = (link.innerText || link.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim();
            return (label === "Next" || label === "Next ›" || label === "Next Page") && link.href;
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
        cards = page.locator(INTERNSHALA_CARDS)
        found = set()
        for i in range(await cards.count()):
            job_id = await read_internshala_job_id(cards.nth(i))
            if job_id:
                found.add(job_id)
        if found and not found <= before:
            return True
    return False
