from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from pocket_agent.forms.engine import UserAnswer, fill_form
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.reader import extract_page
from pocket_agent.forms.workday import WorkdayAccounts, account_scope, new_password

URL = "https://example.wd5.myworkdayjobs.com/en-US/External/job/Example/Engineer_R1"


class Vault:
    def __init__(self, password=None, fail=False):
        self.password = password
        self.fail = fail
        self.writes = 0
        self.pending = False

    def get(self, scope, email):
        return self.password

    def is_pending(self, scope, email):
        return self.pending

    def put(self, scope, email, password, *, pending=False):
        if self.fail:
            raise RuntimeError("vault unavailable")
        self.password = password
        self.pending = pending
        self.writes += 1


def test_account_scope_is_tenant_and_site_bound():
    assert account_scope(URL) == "https://example.wd5.myworkdayjobs.com/External"
    assert account_scope(URL.replace("en-US", "fr-FR")) == account_scope(URL)
    assert account_scope(URL.replace("External", "Other")) != account_scope(URL)
    assert account_scope(URL.replace("example.wd5", "other.wd5")) != account_scope(URL)
    assert account_scope("https://wd5.myworkdaysite.com/recruiting/company/External/job/1") == "https://wd5.myworkdaysite.com/recruiting/company/External"
    for url in (URL.replace("https:", "http:"), URL.replace(".com/", ".com.evil.test/"), "https://example.com", URL.replace("https://", "https://user:pass@")):
        assert account_scope(url) is None


def test_generated_password_complexity():
    password = new_password()
    assert len(password) == 24
    assert any(c.isupper() for c in password)
    assert any(c.islower() for c in password)
    assert any(c.isdigit() for c in password)
    assert any(c in "!@#$%" for c in password)
    assert password != new_password()


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
        page.goto(URL)
        yield page
        browser.close()


def auth_form(creating=True, after="<h1>Verify your email</h1>"):
    import json
    return f'''<form onsubmit='event.preventDefault(); window.requests=(window.requests||0)+1; document.body.innerHTML={json.dumps(after)}'>
      <label>Email<input type="email" data-automation-id="email"></label>
      <label>Password<input type="password"></label>
      {'<label>Verify New Password<input type="password"></label>' if creating else ''}
      <button type="submit">{'Create Account' if creating else 'Sign In'}</button>
      </form>'''


def test_signup_saved_before_verification_and_not_counted_as_application(page, tmp_path: Path):
    page.set_content(auth_form())
    vault = Vault()
    adapter = WorkdayAccounts(URL, "person@example.test", vault, lambda _: pytest.fail("unexpected password prompt"))
    memory = Memory(tmp_path / "forms.sqlite")
    try:
        initial = fill_form(page, URL, memory, None, lambda _: UserAnswer(""), resume=True)
        assert initial.gate == "login"
        assert initial.outcomes == []
        assert adapter.step(page, initial.gate).progressed
        assert vault.writes == 1 and vault.password
        assert page.evaluate("window.requests") == 1
        assert page.evaluate("window.__pocketSubmits") == 0
        assert extract_page(page).gate == "verification"
        assert not adapter.step(page, "verification").progressed
        assert vault.password not in (tmp_path / "forms.sqlite").read_bytes().decode(errors="ignore")
    finally:
        memory.close()


def test_returning_login_resumes_filling_but_never_submits_application(page, tmp_path: Path):
    application = '<h1>My Information</h1><label>First name<input required></label><button type="submit">Submit Application</button>'
    page.set_content(auth_form(creating=False, after=application))
    vault = Vault("KnownPassword!7")
    adapter = WorkdayAccounts(URL, "person@example.test", vault, lambda _: pytest.fail("unexpected prompt"))
    memory = Memory(tmp_path / "forms.sqlite")
    memory.put("first_name", "Jordan", source="resume")
    try:
        fill_form(page, URL, memory, None, lambda _: UserAnswer(""), resume=True)
        assert adapter.step(page, "login").progressed
        result = fill_form(page, URL, memory, None, lambda _: pytest.fail("unexpected ask"), resume=True)
        assert page.locator("input").input_value() == "Jordan"
        assert result.submits == 0
        assert page.evaluate("window.requests") == 1
        assert not result.gate and not result.error
    finally:
        memory.close()


def test_open_signup_and_apply_manually(page):
    page.set_content('<button onclick="this.textContent=\'Apply Manually\'">Apply</button>')
    adapter = WorkdayAccounts(URL, "person@example.test", Vault(), lambda _: "")
    assert adapter.step(page, "start").progressed
    assert adapter.step(page, "start").progressed
    assert not adapter.step(page, "start").progressed
    page.set_content('<input type="password"><a href="#" onclick="event.preventDefault(); document.body.innerHTML=\'<h1>Create Account</h1>\'">Create Account</a>')
    assert adapter.step(page, "login").progressed


def test_keychain_failure_leaves_account_form_untouched(page):
    page.set_content(auth_form())
    adapter = WorkdayAccounts(URL, "person@example.test", Vault(fail=True), lambda _: "")
    assert not adapter.step(page, "login").progressed
    assert page.locator('input[type=password]').first.input_value() == ""
    assert page.locator('input[type=email]').input_value() == ""
    assert page.evaluate("window.requests || 0") == 0


def test_other_tenant_captcha_and_terms_do_not_get_credentials(page):
    page.set_content(auth_form())
    adapter = WorkdayAccounts(URL, "person@example.test", Vault(), lambda _: "")
    assert not adapter.step(page, "captcha").progressed
    page.locator("form").evaluate("form => form.insertAdjacentHTML('afterbegin', '<input type=checkbox required>')")
    assert not adapter.step(page, "login").progressed
    page.goto(URL.replace("example.wd5", "other.wd5"))
    page.set_content(auth_form())
    assert not adapter.step(page, "login").progressed
    assert page.locator('input[type=password]').first.input_value() == ""


def test_failed_signup_existing_account_asks_once_for_existing_password(page):
    page.set_content(auth_form(after='<h1>Account already exists</h1><a href="#" onclick="event.preventDefault()">Sign In</a>'))
    vault = Vault()
    prompts = []
    def password_prompt(message):
        prompts.append(message)
        return "ExistingPassword!9"
    adapter = WorkdayAccounts(URL, "person@example.test", vault, password_prompt)
    assert adapter.step(page, "login").progressed
    generated = vault.password
    assert adapter.step(page, "login").progressed
    page.set_content(auth_form(creating=False, after='<h1>Application</h1>'))
    assert adapter.step(page, "login").progressed
    assert len(prompts) == 1
    assert vault.password == "ExistingPassword!9" and vault.password != generated
    page.set_content(auth_form(creating=False, after='<h1>Incorrect password</h1>'))
    assert not adapter.step(page, "login").progressed
    assert len(prompts) == 1


def test_workday_header_signin_does_not_confuse_account_controls(page):
    page.set_content('<button data-automation-id="utilityButtonSignIn">Sign In</button>' + auth_form(creating=False, after='<h1>Application</h1>'))
    page.get_by_role('button', name='Sign In', exact=True).last.evaluate("el => el.setAttribute('data-automation-id', 'signInSubmitButton')")
    adapter = WorkdayAccounts(URL, 'person@example.test', Vault('SavedPassword!9'), lambda _: pytest.fail('unexpected prompt'))
    assert adapter.step(page, 'login').progressed
    assert page.locator('h1').inner_text() == 'Application'


def test_saved_account_opens_workday_signin_instead_of_registering_again(page):
    page.set_content('<button>Sign In</button>' + auth_form() + '<button data-automation-id="signInLink" onclick="document.body.innerHTML=\'<h1>Sign In screen</h1>\'">Sign In</button>')
    vault = Vault('SavedPassword!9')
    adapter = WorkdayAccounts(URL, 'person@example.test', vault, lambda _: pytest.fail('unexpected prompt'))
    assert adapter.step(page, 'login').progressed
    assert page.locator('h1').inner_text() == 'Sign In screen'
    assert vault.writes == 0


def test_authorized_workday_account_consent_is_checked_automatically(page):
    page.set_content(auth_form())
    page.locator('form').evaluate("form => form.insertAdjacentHTML('afterbegin', '<input type=checkbox required data-automation-id=createAccountCheckbox>')")
    page.locator('form').evaluate("form => form.addEventListener('submit', () => { window.accountConsent = form.querySelector('[data-automation-id=createAccountCheckbox]').checked; }, true)")
    adapter = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: pytest.fail('unexpected prompt'))
    assert adapter.step(page, 'login').progressed
    assert page.evaluate('window.accountConsent') is True
    assert page.evaluate('window.requests') == 1


def test_decorative_checkbox_overlay_does_not_block_consent(page):
    page.set_content(auth_form())
    page.locator('form').evaluate("""form => form.insertAdjacentHTML('afterbegin',
        '<div style="position:relative;width:20px;height:20px"><input type=checkbox required data-automation-id=createAccountCheckbox><div style="position:absolute;inset:0;background:gray"></div></div>')""")
    adapter = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: '')
    assert adapter.step(page, 'login').progressed
    assert page.evaluate('window.requests') == 1


def test_pending_password_does_not_redirect_to_nonexistent_account(page):
    page.set_content(auth_form())
    vault = Vault('PendingPassword!7')
    vault.pending = True
    adapter = WorkdayAccounts(URL, 'person@example.test', vault, lambda _: pytest.fail('unexpected prompt'))
    assert adapter.step(page, 'login').progressed
    assert page.evaluate('window.requests') == 1
    assert vault.password == 'PendingPassword!7'
    assert vault.pending is True  # Still awaiting email verification.


def test_sample_resume_email_can_be_used_for_testing(page):
    page.set_content(auth_form())
    vault = Vault()
    adapter = WorkdayAccounts(URL, 'jordan.hale@example.com', vault, lambda _: '')
    step = adapter.step(page, 'login')
    assert step.progressed
    assert vault.writes == 1
    assert page.evaluate('window.requests') == 1


def test_failure_reports_stage_without_password(page, monkeypatch):
    page.set_content(auth_form())
    adapter = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: '')
    def broken_click(*args):
        raise RuntimeError('secret-value-DO-NOT-PRINT')
    monkeypatch.setattr(adapter, '_click', broken_click)
    step = adapter.step(page, 'login')
    assert not step.progressed
    assert 'sending the account request (RuntimeError)' in step.message
    assert 'secret-value' not in step.message


def test_account_uses_resume_email_for_email_and_username(page):
    page.set_content(auth_form())
    page.locator('form').evaluate("form => form.insertAdjacentHTML('afterbegin', '<input name=username>')")
    page.locator('form').evaluate("form => form.addEventListener('submit', () => { window.accountUsername = form.querySelector('[name=username]').value; window.accountEmail = form.querySelector('[type=email]').value; }, true)")
    adapter = WorkdayAccounts(URL, 'resume@personal.test', Vault(), lambda _: pytest.fail('unexpected prompt'), default_password='ConfiguredPassword!1')
    assert adapter.step(page, 'login').progressed
    assert page.evaluate('window.accountUsername') == 'resume@personal.test'
    assert page.evaluate('window.accountEmail') == 'resume@personal.test'


def test_password_requirement_failure_is_reported_without_signup(page):
    page.set_content('<p>An uppercase character. A special character.</p>' + auth_form())
    adapter = WorkdayAccounts(URL, 'resume@personal.test', Vault(), lambda _: '', default_password='bananaboy123')
    step = adapter.step(page, 'login')
    assert not step.progressed and 'requirements' in step.message
    assert page.evaluate('window.requests || 0') == 0


def test_workday_click_filter_overlay_is_clicked_instead_of_covered_button(page):
    page.set_content(auth_form())
    page.locator('button').evaluate("""button => {
        button.setAttribute('data-automation-id', 'createAccountSubmitButton');
        button.setAttribute('aria-hidden', 'true');
        button.style.cssText = 'position:absolute;inset:0;width:100%;height:40px';
        const container = document.createElement('div');
        container.style.cssText = 'position:relative;width:200px;height:40px';
        button.replaceWith(container); container.append(button);
        const overlay = document.createElement('div');
        overlay.setAttribute('role', 'button'); overlay.setAttribute('aria-label', 'Create Account');
        overlay.setAttribute('data-automation-id', 'click_filter');
        overlay.style.cssText = 'position:absolute;inset:0;z-index:2';
        overlay.onclick = () => { window.overlayClicked = true; button.form.requestSubmit(); };
        container.append(overlay);
    }""")
    adapter = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: '')
    assert adapter.step(page, 'login').progressed
    assert page.evaluate('window.overlayClicked') is True
    assert page.evaluate('window.requests') == 1


def test_click_filter_inserted_after_filling_is_resolved_at_click_time(page):
    page.set_content(auth_form())
    page.locator('button').evaluate("el => el.setAttribute('data-automation-id','createAccountSubmitButton')")
    page.locator('input[type=password]').last.evaluate("""input => input.addEventListener('input', () => {
        if (document.querySelector('[data-automation-id=click_filter]')) return;
        const button = document.querySelector('button');
        button.setAttribute('aria-hidden','true');
        const container = document.createElement('div');
        container.style.cssText = 'position:relative;width:200px;height:40px';
        button.replaceWith(container); container.append(button);
        button.style.cssText = 'position:absolute;inset:0';
        const overlay = document.createElement('div');
        overlay.setAttribute('role','button'); overlay.setAttribute('aria-label','Create Account');
        overlay.setAttribute('data-automation-id','click_filter');
        overlay.style.cssText = 'position:absolute;inset:0;z-index:2';
        overlay.onclick = () => { window.dynamicOverlayClicked=true; button.form.requestSubmit(); };
        container.append(overlay);
    })""")
    adapter = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: '')
    step = adapter.step(page, 'login')
    assert step.progressed, step.message
    assert page.evaluate('window.dynamicOverlayClicked') is True
    assert page.evaluate('window.requests') == 1


def test_registration_requires_keyboard_validation_before_click(page):
    page.set_content(auth_form())
    page.locator('input[type=password]').last.evaluate("""input => {
        input.addEventListener('keyup', () => window.passwordKeyup = true);
        input.addEventListener('blur', () => window.passwordBlur = true);
        input.form.addEventListener('submit', event => {
            if (!window.passwordKeyup || !window.passwordBlur) event.stopImmediatePropagation();
        }, true);
    }""")
    step = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: '').step(page, 'login')
    assert step.progressed, step.message
    assert page.evaluate('window.passwordKeyup && window.passwordBlur')
    assert page.evaluate('window.requests') == 1


def test_workday_registration_rejection_is_not_reported_as_success(page):
    page.set_content(auth_form(after='<h1>Create Account</h1><div>Invalid Username/Password</div><input type=password>'))
    step = WorkdayAccounts(URL, 'person@example.test', Vault(), lambda _: '').step(page, 'login')
    assert page.evaluate('window.requests') == 1
    assert not step.progressed
    assert 'Workday rejected' in step.message
    assert 'Invalid Username/Password' in step.message
