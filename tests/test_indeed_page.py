import pytest
from playwright.async_api import async_playwright

from src.indeed_main import QUESTION_ROOT, IndeedRunner
from src.indeed_page import (
    card_is_indeed_apply,
    card_should_skip,
    find_indeed_apply_link,
    has_indeed_company_site,
    indeed_apply_is_blocked,
    indeed_apply_succeeded,
    indeed_experience_is_fresher,
    indeed_job_key,
    indeed_posted_recently,
    indeed_search_url,
    is_placeholder_job_id,
    load_more_indeed_results,
    read_indeed_job_id,
    read_indeed_title,
)
from src.modal_navigator import ModalNavigator, NavigationResult
from src.solver import ScreeningSolver
from src.stealth import StealthController

PRIMARY = ["javascript", "react", "express", "node", "mongo", "mern"]
STACKS = ["java", "wordpress", "python", "react native", "react-native"]
SENIORITY = ["senior", "lead"]

APPLY_FORM = """
<main>
  <div class="ia-BasePage-component">
    <label for="years">How many years of work experience do you have with React.js?</label>
    <input id="years" type="number" />
    <button data-testid="continue-button" id="send">Continue</button>
  </div>
</main>
<script>
  document.getElementById("send").addEventListener("click", () => {
    document.body.dataset.years = document.getElementById("years").value;
    document.body.insertAdjacentHTML("beforeend", "<p>Your application has been submitted</p>");
  });
</script>
"""


def test_indeed_search_is_entry_level_and_last_day():
    url = indeed_search_url("React.js")
    assert "fromage=1" in url
    assert "ENTRY_LEVEL" in url
    assert "q=React.js" in url
    assert url.startswith("https://in.indeed.com/jobs?")
    assert indeed_job_key("abc123") == "indeed:abc123"


def test_indeed_titles_and_apply_badge():
    assert card_should_skip("React.js Developer", "", PRIMARY, STACKS, SENIORITY) is None
    assert card_should_skip("React Native Developer", "", PRIMARY, STACKS, SENIORITY) == "stack"
    assert card_should_skip("Java Full Stack Developer", "", PRIMARY, STACKS, SENIORITY) == "stack"
    assert card_should_skip(
        "Hiring AEM + React.js Engineers", "", PRIMARY, STACKS + ["aem"], SENIORITY
    ) == "stack"
    assert card_should_skip(
        "Linux Solution Consultant", "", ["linux"], [], SENIORITY + ["consultant"]
    ) == "seniority"
    assert card_is_indeed_apply("React Developer Easily apply Novami") is True
    assert card_is_indeed_apply("React Developer Infosys") is False
    assert indeed_experience_is_fresher("0-1 years") is True
    assert indeed_experience_is_fresher("React Developer 2 years of experience") is False
    assert indeed_posted_recently("Just posted") is True
    assert indeed_posted_recently("3 days ago") is False
    assert indeed_posted_recently("") is None
    assert is_placeholder_job_id("123456789abcdef0") is True
    assert is_placeholder_job_id("f1e2d3c4b5a67890") is True
    assert is_placeholder_job_id("80f6a8445532cde0") is False
    assert indeed_apply_is_blocked("Something went wrong. We're unable to process the application") is True


@pytest.mark.asyncio
async def test_indeed_apply_ignores_company_site():
    html = """
    <a href="https://example.com/jobs">Apply on company site</a>
    <a href="https://smartapply.indeed.com/beta/indeedapply/form/resume">Apply now</a>
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        link = await find_indeed_apply_link(page)
        assert link is not None
        assert "smartapply.indeed.com" in (await link.get_attribute("href"))
        await page.set_content("<button>Apply on company site</button>")
        assert await find_indeed_apply_link(page) is None
        assert await has_indeed_company_site(page) is True
        await browser.close()


@pytest.mark.asyncio
async def test_indeed_apply_fills_fresher_experience_and_confirms():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(APPLY_FORM)
        runner = IndeedRunner(db_path=":memory:")
        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=ScreeningSolver(profile_path="config/profile.json", gemini_api_key=None),
            modal_selector=QUESTION_ROOT,
        )
        result, reason = await runner._fill_apply_form(page, stealth, navigator)
        assert result == NavigationResult.SUBMITTED, reason
        assert await page.evaluate("() => document.body.dataset.years") == "0"
        assert await indeed_apply_succeeded(page) is True
        await page.set_content(
            "<main><p>Something went wrong. We're unable to process the application for this job.</p></main>"
        )
        blocked, blocked_reason = await runner._fill_apply_form(page, stealth, navigator)
        assert blocked_reason == "Indeed cannot process this application"
        assert blocked == NavigationResult.FAILED
        await browser.close()


@pytest.mark.asyncio
async def test_indeed_next_page_loads_more_jobs():
    first = """
    <div class="job_seen_beacon">
      <a class="jcs-JobTitle" data-jk="111">React.js Developer</a>
    </div>
    <a href="https://jobs.test/page2" aria-label="Next Page">Next</a>
    """
    second = """
    <div class="job_seen_beacon">
      <a class="jcs-JobTitle" data-jk="222">Node.js Developer</a>
    </div>
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def fulfill(route):
            body = second if route.request.url.endswith("/page2") else first
            await route.fulfill(status=200, content_type="text/html", body=body)

        await page.route("**/*", fulfill)
        await page.goto("https://jobs.test/page1")
        assert await read_indeed_job_id(page.locator(".job_seen_beacon")) == "111"
        assert await read_indeed_title(page.locator(".job_seen_beacon")) == "React.js Developer"
        assert await load_more_indeed_results(page) is True
        assert await read_indeed_job_id(page.locator(".job_seen_beacon")) == "222"
        await browser.close()
