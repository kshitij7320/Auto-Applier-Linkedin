import pytest
from playwright.async_api import async_playwright

from src.job_page import (
    find_easy_apply_button,
    is_auth_wall,
    load_more_results,
    posted_within_hours,
    read_job_id,
    title_priority,
    wait_for_selected_job,
)


HIDDEN_DECOY_HTML = """
<div class="jobs-details" data-job-id="111">
  <a href="/jobs/view/111/">Full Stack Developer</a>
  <h1>Full Stack Developer</h1>
  <div class="jobs-apply-button--top-card" style="display:none">
    <button class="jobs-apply-button">Easy Apply</button>
  </div>
  <div class="jobs-s-apply">
    <button id="jobs-apply-button-id" class="jobs-apply-button"
            aria-label="Easy Apply to Full Stack Developer at Acme">
      <span class="artdeco-button__text">Easy Apply</span>
    </button>
  </div>
</div>
"""

UNWRAPPED_BUTTON_HTML = """
<div class="jobs-search__job-details">
  <div class="jobs-s-apply">
    <button id="jobs-apply-button-id" class="jobs-apply-button" aria-label="Easy Apply to Backend Developer at Beta">
      Easy Apply
    </button>
  </div>
</div>
"""

PAGINATION_HTML = """
<button id="decoy" aria-label="Next">Next</button>
<ul id="list">
  <li data-occludable-job-id="1"></li>
</ul>
<div class="jobs-search-pagination">
  <ul>
    <li class="active selected"><button aria-label="Page 1" aria-current="true">1</button></li>
    <li><button id="real-next" aria-label="Page 2">2</button></li>
  </ul>
</div>
<script>
  document.getElementById("decoy").addEventListener("click", () => {
    document.body.dataset.decoy = "1";
  });
  document.getElementById("real-next").addEventListener("click", () => {
    const item = document.createElement("li");
    item.setAttribute("data-occludable-job-id", "2");
    document.getElementById("list").appendChild(item);
  });
</script>
"""


def test_job_title_drops_linkedin_verification_suffix():
    from src.job_page import normalize_job_title
    assert normalize_job_title("Frontend Developer intern with verification") == "Frontend Developer intern"
    assert normalize_job_title("Full Stack Developer Intern\nFull Stack Developer Intern") == "Full Stack Developer Intern"


def test_wordpress_and_other_stacks_are_rejected():
    from src.job_page import title_rejection_reason
    required = ["react", "node", "express", "mongo", "mern", "javascript", "full stack", "frontend", "backend"]
    stacks = ["wordpress", "php", "python", "java"]
    seniority = ["senior", "lead"]
    assert title_rejection_reason("WordPress Web Developer Intern", required, stacks, seniority) == "stack"
    assert title_rejection_reason("Associate (Python) Software Engineer", required, stacks, seniority) == "stack"
    assert title_rejection_reason("Java Developer", required, stacks, seniority) == "stack"
    assert title_rejection_reason("Java Full Stack Developer", required, stacks, seniority) == "stack"
    assert title_rejection_reason("Spring Boot Full Stack Developer", required, ["spring"], seniority) == "stack"
    assert title_rejection_reason("JavaScript Full Stack Developer", required, stacks, seniority) is None
    assert title_rejection_reason("React.js Developer", required, stacks, seniority) is None
    assert title_rejection_reason("JavaScript Developer", required, stacks, seniority) is None
    assert title_rejection_reason("Node.js Developer Intern", required, stacks, seniority) is None
    assert title_rejection_reason("Software Engineer", required, stacks, seniority) == "role"
    primary = ["javascript", "react", "express", "node", "mongo", "mern"]
    secondary = ["redux", "sql", "html", "css"]
    assert title_rejection_reason("Full Stack Developer", primary, stacks, seniority) == "role"
    assert title_rejection_reason("React Native Developer", primary, stacks + ["react native", "react-native"], seniority) == "stack"
    assert title_rejection_reason("React.js Developer", primary, stacks + ["react native"], seniority) is None
    assert title_rejection_reason("Redux Developer", primary, stacks, seniority) == "role"
    assert title_rejection_reason("Redux Developer", secondary, stacks, seniority) is None


def test_primary_stack_titles_are_applied_before_generic_titles():
    skills = ["React.js", "Node.js", "Express.js", "MongoDB"]
    assert title_priority("React.js Developer Intern", skills) < title_priority("Software Engineer", skills)
    assert title_priority("Node.js Developer", skills) == 0
    assert title_priority("MERN Stack Intern", skills) == 0


def test_only_about_the_last_day_counts_as_recent():
    assert posted_within_hours("2 hours ago") is True
    assert posted_within_hours("1 day ago") is True
    assert posted_within_hours("3 days ago") is False
    assert posted_within_hours("1 week ago") is False
    assert posted_within_hours("") is None


@pytest.mark.asyncio
async def test_easy_apply_ignores_hidden_duplicate_and_uses_visible_button():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HIDDEN_DECOY_HTML)

        button = await find_easy_apply_button(page)
        assert button is not None
        assert await button.get_attribute("id") == "jobs-apply-button-id"
        await browser.close()


@pytest.mark.asyncio
async def test_selected_job_is_recognized_from_the_details_pane():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HIDDEN_DECOY_HTML)
        assert await wait_for_selected_job(page, "111", "Full Stack Developer") is True
        await browser.close()


@pytest.mark.asyncio
async def test_easy_apply_finds_button_without_top_card_wrapper():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(UNWRAPPED_BUTTON_HTML)

        button = await find_easy_apply_button(page)
        assert button is not None
        label = ((await button.inner_text()) + " " + (await button.get_attribute("aria-label") or "")).lower()
        assert "easy apply" in label
        await browser.close()


@pytest.mark.asyncio
async def test_job_id_is_read_from_card_link_when_attribute_missing():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div class="job-card-container"><a href="/jobs/view/9876543210/?trk=public_jobs">Role</a></div>'
        )
        card = page.locator(".job-card-container")
        assert await read_job_id(card) == "9876543210"
        await browser.close()


@pytest.mark.asyncio
async def test_auth_wall_detected_from_login_form():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<form class="login__form"><input id="username" /></form>')
        assert await is_auth_wall(page) is True
        await browser.close()


@pytest.mark.asyncio
async def test_pagination_uses_search_pager_not_unrelated_next_button():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(PAGINATION_HTML)

        assert await load_more_results(page) is True
        assert await page.locator("[data-occludable-job-id='2']").count() == 1
        assert (await page.evaluate("() => document.body.dataset.decoy || ''")) == ""
        await browser.close()
