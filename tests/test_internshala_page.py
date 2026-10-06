import pytest
from playwright.async_api import async_playwright

from src.internshala_main import QUESTION_ROOT, InternshalaRunner
from src.internshala_page import (
    card_is_easy_apply,
    internshala_job_key,
    internshala_posted_recently,
    internshala_search_urls,
    internship_experience_is_fresher,
    internship_should_skip,
    load_more_internshala_results,
    read_internshala_job_id,
    read_internshala_title,
)
from src.modal_navigator import ModalNavigator, NavigationResult
from src.solver import ScreeningSolver
from src.stealth import StealthController

PRIMARY = ["javascript", "react", "express", "node", "mongo", "mern"]
STACKS = ["java", "python", "wordpress", ".net"]
SENIORITY = ["senior", "lead", "consultant"]

APPLY_FORM = """
<div id="questions" style="display:none">Additional Questions</div>
<div id="easy_apply_modal">
  <label for="radio1">Yes, I am available to join immediately</label>
  <input id="radio1" type="radio" name="confirm_availability" value="yes" />
  <input id="last_working_date" type="text" placeholder="When is the latest you can join?" style="display:none" />
  <div class="form-group additional_question">
    <div class="assessment_question"><label>Do you have a working laptop and internet?</label></div>
    <label for="Yes_2">Yes</label>
    <input id="Yes_2" type="radio" name="custom_question_radio_2" value="Yes" aria-required="true" />
    <label for="No_2">No</label>
    <input id="No_2" type="radio" name="custom_question_radio_2" value="No" aria-required="true" />
  </div>
  <div class="form-group additional_question">
    <div class="assessment_question"><label>How many months of experience do you have in React.js?</label></div>
    <input id="months" type="number" placeholder="Enter numeric value" aria-required="true" />
  </div>
  <div class="form-group additional_question">
    <div class="assessment_question"><label>Please share a link to your portfolio/work samples</label></div>
    <textarea id="portfolio" placeholder="Enter text ..." aria-required="true"></textarea>
  </div>
  <input id="submit" type="button" value="Submit" />
</div>
<script>
  document.getElementById("submit").addEventListener("click", () => {
    const yes = document.getElementById("Yes_2");
    const months = document.getElementById("months").value;
    const portfolio = document.getElementById("portfolio").value;
    if (!months || !portfolio) {
      document.body.dataset.failed = "1";
      return;
    }
    document.body.dataset.laptop = yes.checked ? "Yes" : "";
    document.body.dataset.months = months;
    document.body.dataset.portfolio = portfolio;
    document.body.dataset.hidden = document.getElementById("last_working_date").value;
    document.body.insertAdjacentHTML("beforeend", "<p>Application submitted</p>");
  });
</script>
"""


def test_internshala_search_covers_internships_and_jobs():
    urls = internshala_search_urls("React.js")
    assert urls[0] == "https://internshala.com/internships/keywords-react/"
    assert urls[1] == "https://internshala.com/jobs/keywords-react/"
    assert internshala_job_key("3311211") == "internshala:3311211"


def test_internshala_keeps_mern_and_rejects_other_stacks():
    assert internship_should_skip("MERN Stack Developer", "MongoDB Node.js React", PRIMARY, STACKS, SENIORITY) is None
    assert internship_should_skip(
        "Software Development", "Java MySQL JavaScript Python .NET", PRIMARY, STACKS, SENIORITY
    ) == "stack"
    assert internship_should_skip("Web Development", "HTML CSS JavaScript", PRIMARY, STACKS, SENIORITY) is None
    assert internship_should_skip("Python Developer", "React", PRIMARY, STACKS, SENIORITY) == "stack"
    assert internship_should_skip(
        "Product Designer - UX/UI Focus", "JavaScript React", PRIMARY, STACKS, SENIORITY + ["designer", "ux/ui"]
    ) == "seniority"
    assert card_is_easy_apply("individual_internship easy_apply") is True
    assert card_is_easy_apply("individual_internship") is False
    assert internship_experience_is_fresher("No experience required") is True
    assert internship_experience_is_fresher("1 year(s)") is False
    assert internship_experience_is_fresher("3 Months") is None
    assert internshala_posted_recently("Few hours ago") is True
    assert internshala_posted_recently("Today") is True
    assert internshala_posted_recently("2 weeks ago") is False


@pytest.mark.asyncio
async def test_internshala_apply_confirms_and_answers_zero_years():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(APPLY_FORM)
        runner = InternshalaRunner(db_path=":memory:")
        stealth = StealthController(page)
        navigator = ModalNavigator(
            page=page,
            stealth=stealth,
            solver=ScreeningSolver(profile_path="config/profile.json", gemini_api_key=None),
            modal_selector=QUESTION_ROOT,
        )
        result, reason = await runner._fill_apply_form(page, stealth, navigator)
        assert result == NavigationResult.SUBMITTED, reason
        answers = await page.evaluate(
            "() => ({laptop: document.body.dataset.laptop, months: document.body.dataset.months, portfolio: document.body.dataset.portfolio, hidden: document.body.dataset.hidden})"
        )
        assert answers["laptop"] == "Yes"
        assert answers["months"] == "0"
        assert "http" in (answers["portfolio"] or "")
        assert answers["hidden"] == ""
        await browser.close()


@pytest.mark.asyncio
async def test_internshala_next_page_and_card_id():
    first = """
    <div id="individual_internship_111" class="individual_internship easy_apply">
      <a class="job-title-href">React Developer</a>
    </div>
    <a href="https://jobs.test/page2">Next</a>
    """
    second = """
    <div id="individual_internship_222" class="individual_internship easy_apply">
      <h3 class="job-internship-name">Node Developer</h3>
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
        card = page.locator(".individual_internship")
        assert await read_internshala_job_id(card) == "111"
        assert await read_internshala_title(card) == "React Developer"
        assert await load_more_internshala_results(page) is True
        assert await read_internshala_job_id(page.locator(".individual_internship")) == "222"
        await browser.close()
