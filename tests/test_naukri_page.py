import pytest
from playwright.async_api import async_playwright

from src.job_page import title_rejection_reason
from src.naukri_page import (
    card_should_skip,
    experience_is_fresher,
    find_naukri_apply_button,
    has_company_site_apply,
    is_on_site_apply_label,
    is_walk_in,
    load_more_naukri_results,
    naukri_apply_succeeded,
    naukri_job_key,
    naukri_posted_recently,
    naukri_search_url,
    read_naukri_job_id,
    read_naukri_title,
)
from src.naukri_main import NaukriRunner, QUESTION_ROOT
from src.modal_navigator import ModalNavigator, NavigationResult
from src.solver import ScreeningSolver
from src.stealth import StealthController

PRIMARY = ["javascript", "react", "express", "node", "mongo", "mern"]
STACKS = ["java", "wordpress", "python", "react native", "react-native"]
SENIORITY = ["senior", "lead"]

APPLY_PAGE = """
<div class="srp-jobtuple-wrapper" data-job-id="111">
  <a class="title" href="https://www.naukri.com/job-listings-react-js-developer-acme-111">React.js Developer</a>
  <a class="comp-name">Acme</a>
  <span class="exp-wrap">0-1 Yrs</span>
  <span class="job-post-day">Few hours ago</span>
</div>
<button id="apply-button">Apply</button>
<div class="naukri-apply-questions" style="display:none">
  <label for="years">How many years of work experience do you have with React.js?</label>
  <input id="years" type="number" />
  <button id="send">Submit</button>
</div>
<script>
  document.getElementById("apply-button").addEventListener("click", () => {
    document.querySelector(".naukri-apply-questions").style.display = "block";
  });
  document.getElementById("send").addEventListener("click", () => {
    document.body.dataset.years = document.getElementById("years").value;
    document.body.insertAdjacentHTML("beforeend", "<p>You have successfully applied</p>");
  });
</script>
"""


def test_naukri_search_is_fresher_and_last_day():
    url = naukri_search_url("React.js")
    assert "experience=0-1" in url
    assert "jobAge=1" in url
    assert "k=React.js" in url
    assert naukri_job_key("111") == "naukri:111"


def test_on_site_apply_ignores_company_site_and_walk_ins():
    assert is_on_site_apply_label("Apply") is True
    assert is_on_site_apply_label("Quick apply") is True
    assert is_on_site_apply_label("Apply on company website") is False
    assert is_on_site_apply_label("Interested") is False


def test_naukri_titles_stay_on_the_primary_stack():
    assert card_should_skip("React.js Developer", "", PRIMARY, STACKS, SENIORITY) is None
    assert card_should_skip("React Native Developer", "", PRIMARY, STACKS, SENIORITY) == "stack"
    assert card_should_skip("Java Full Stack Developer", "", PRIMARY, STACKS, SENIORITY) == "stack"
    assert card_should_skip("WordPress Developer", "", PRIMARY, STACKS, SENIORITY) == "stack"
    assert experience_is_fresher("0-1 Yrs") is True
    assert experience_is_fresher("2-4 Yrs") is False
    assert naukri_posted_recently("Few hours ago") is True
    assert naukri_posted_recently("3 days ago") is False
    assert naukri_posted_recently("Starts in 1-3 months") is None
    assert is_walk_in("Walk-in || Node.Js Full Stack Developer") is True
    mashed = "AngularJavascriptReact.jsPostgresqlFull StackNode.jsHTML"
    assert title_rejection_reason(mashed, [], ["jsp", "java", "c#"], []) is None


@pytest.mark.asyncio
async def test_naukri_apply_fills_fresher_experience_and_confirms():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(APPLY_PAGE)
        card = page.locator(".srp-jobtuple-wrapper")
        assert await read_naukri_job_id(card) == "111"
        assert await read_naukri_title(card) == "React.js Developer"
        button = await find_naukri_apply_button(page)
        assert button is not None

        runner = NaukriRunner(db_path=":memory:")
        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=ScreeningSolver(profile_path="config/profile.json", gemini_api_key=None),
            modal_selector=QUESTION_ROOT,
        )
        result, reason = await runner._complete_apply(page, stealth, navigator)
        assert result == NavigationResult.SUBMITTED, reason
        assert await page.evaluate("() => document.body.dataset.years") == "0"
        assert await naukri_apply_succeeded(page) is True
        await browser.close()


@pytest.mark.asyncio
async def test_quick_apply_is_accepted_and_company_site_is_not():
    html = """
    <a class="apply-button">Apply on company website</a>
    <div class="styles_apply-button__abc"><button>Quick apply</button></div>
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        button = await find_naukri_apply_button(page)
        assert button is not None
        assert "quick apply" in (await button.inner_text()).lower()
        await page.set_content('<button id="company-site-button">Apply on company site</button>')
        assert await find_naukri_apply_button(page) is None
        assert await has_company_site_apply(page) is True
        await browser.close()


@pytest.mark.asyncio
async def test_experience_skip_counts_as_a_primary_skill_match():
    html = """
    <div class="srp-jobtuple-wrapper" data-job-id="222">
      <a class="title" href="/job-listings-react-js-developer-222">React.js Developer</a>
      <span class="comp-name">Acme</span>
      <span class="exp-wrap">3-6 Yrs</span>
      <span class="job-post-day">Just now</span>
    </div>
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        runner = NaukriRunner(db_path=":memory:")
        runner.active_title_terms = PRIMARY
        runner.excluded_stacks = STACKS
        runner.excluded_titles = SENIORITY
        stats = {"applied": 0, "discarded": 0, "failed": 0, "skipped": 0}
        matched = await runner._consider_job(
            page,
            StealthController(page),
            ModalNavigator(page=page, stealth=StealthController(page), solver=ScreeningSolver(
                profile_path="config/profile.json", gemini_api_key=None
            ), modal_selector=QUESTION_ROOT),
            page.locator(".srp-jobtuple-wrapper"),
            "222",
            stats,
        )
        assert matched is True
        assert stats["skipped"] == 1
        assert len(page.context.pages) == 1
        await browser.close()


@pytest.mark.asyncio
async def test_naukri_next_page_loads_more_jobs():
    first = """
    <div class="srp-jobtuple-wrapper" data-job-id="111">
      <a class="title">React.js Developer</a>
    </div>
    <a href="https://jobs.test/page2"><span>Next</span></a>
    """
    second = """
    <div class="srp-jobtuple-wrapper" data-job-id="222">
      <a class="title">Node.js Developer</a>
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
        assert await load_more_naukri_results(page) is True
        assert await read_naukri_job_id(page.locator(".srp-jobtuple-wrapper")) == "222"
        await browser.close()
