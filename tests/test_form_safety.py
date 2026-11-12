"""Login walls, captchas, and final-review buttons are not clicked."""

from pathlib import Path

from playwright.sync_api import sync_playwright

from pocket_agent.forms.engine import fill_form
from pocket_agent.forms.evaluate import scripted_user
from pocket_agent.forms.hard_catalog import HARD_FORMS
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.reader import extract_page
from pocket_agent.forms.seed import eval_profile, seed_memory
from pocket_agent.forms.server import FormServer
from pocket_agent.resume_pdf import write_simple_pdf


def test_captcha_page_is_detected() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<h1>Verify you are human</h1><button type='submit'>Continue</button>")
        snap = extract_page(page)
        browser.close()
    assert snap.gate == "captcha"


def test_login_wall_and_review_continue_do_not_submit(tmp_path: Path) -> None:
    resume = tmp_path / "resume.pdf"
    write_simple_pdf(resume, ["Jordan Hale", "Fictional resume"])
    forms = tuple(form for form in HARD_FORMS if form.id in {"hd-login", "hh-account", "hd-review", "hh-essay"})
    server = FormServer(forms)
    server.start()
    memory = Memory(tmp_path / "mem.sqlite")
    seed_memory(memory, eval_profile(), resume_path=str(resume))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for form in forms:
                page = browser.new_page()
                run = fill_form(page, server.url(form.id), memory, resume, scripted_user, None)
                assert run.submits == 0, form.id
                assert run.error is None, run.error
                if form.gate:
                    assert run.gate == form.gate
                    assert not any(item.action == "fill" and item.value for item in run.outcomes)
                page.close()
            browser.close()
    finally:
        memory.close()
        server.stop()


def test_name_confirmation_checks_actual_entered_name(tmp_path: Path) -> None:
    from pocket_agent.forms.engine import FormRun, UserAnswer, _decide_and_write
    from pocket_agent.forms.memory import Memory
    from pocket_agent.forms.reader import extract_page

    memory = Memory(tmp_path / "names.sqlite")
    memory.put("full_name", "Jordan Alex Hale", source="resume")
    question = "Have you added your full legal name and surname (including any middle names)? This is just so our systems can match you correctly."
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            for entered, asked in (("Jordan Alex", False), ("Jordan", True)):
                page.set_content(f'''<label>First name<input value="{entered}"></label>
                    <label>Last name<input value="Hale"></label>
                    <label>{question}<select required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>''')
                snapshot = extract_page(page)
                prompts = []
                def ask(item):
                    prompts.append(item)
                    return UserAnswer("")
                outcome = _decide_and_write(page, snapshot.fields[-1], memory, None, ask, None, FormRun(url="test"), snapshot=snapshot)
                assert bool(prompts) is asked
                assert outcome.asked is asked
                assert page.locator("select").input_value() == ("" if asked else "Yes")
            browser.close()
    finally:
        memory.close()
