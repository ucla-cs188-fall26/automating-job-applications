"""Opt-in Workday account navigation. Credentials never enter form memory or traces."""
from __future__ import annotations

import re
import json
import secrets
import string
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit


def account_scope(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in {None, 443}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if host.endswith(".myworkdayjobs.com"):
        if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
            parts.pop(0)
        if not parts or parts[0] in {"job", "login", "userHome"}:
            return None
        site = parts[0]
    elif host.endswith(".myworkdaysite.com"):
        if len(parts) < 3 or parts[0] != "recruiting":
            return None
        site = "/".join(parts[:3])
    else:
        return None
    return f"https://{host}/{site}"


class KeychainCredentials:
    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Workday account automation currently requires macOS Keychain.")
        try:
            from keyring.backends.macOS import Keyring
        except ImportError as exc:
            raise RuntimeError("Install account support: uv sync --extra accounts") from exc
        self.backend = Keyring()  # Explicit backend; never fall back to a plaintext store.

    def get(self, scope: str, email: str) -> str | None:
        raw = self.backend.get_password("pocket-agent:workday:" + scope, email)
        if raw and raw.startswith('{'):
            return json.loads(raw)["password"]
        return raw

    def is_pending(self, scope: str, email: str) -> bool:
        raw = self.backend.get_password("pocket-agent:workday:" + scope, email)
        if raw and raw.startswith('{'):
            return bool(json.loads(raw).get("pending", True))
        return bool(raw)  # Older versions saved before signup, without recording success.

    def put(self, scope: str, email: str, password: str, *, pending: bool = False) -> None:
        self.backend.set_password("pocket-agent:workday:" + scope, email, json.dumps({"password": password, "pending": pending}))


def new_password() -> str:
    # Guarantee all common complexity classes; shuffle rather than fix their positions.
    chars = [secrets.choice(pool) for pool in (string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%")]
    chars += [secrets.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(20)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


@dataclass
class AccountStep:
    progressed: bool = False
    message: str = ""


def _visible(locator):
    return [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]


class WorkdayAccounts:
    def __init__(self, url: str, email: str, credentials, password_prompt, *, default_password: str | None = None) -> None:
        self.scope = account_scope(url)
        self.email = email
        self.credentials = credentials
        self.password_prompt = password_prompt
        self.default_password = default_password
        self.attempts: set[tuple[str, str]] = set()
        self.recover_existing = False
        self.generated_password = False
        self.stage = "opening the account page"

    def _click(self, page, button) -> bool:
        # Resolve the target AFTER filling and consent: Workday can insert its
        # click_filter during React validation, replacing a previously clickable button.
        if button.get_attribute("aria-hidden") == "true":
            name = button.inner_text().strip()
            overlays = _visible(page.locator('[data-automation-id="click_filter"]').and_(
                page.get_by_role("button", name=name, exact=True)
            ))
            if len(overlays) != 1:
                raise RuntimeError("Workday account control is ambiguous")
            button = overlays[0]
        # Account creation/sign-in can submit an auth form. These are not job submissions.
        button.evaluate("el => { const form = el.closest('form'); if (form) form.dataset.pocketAccount = 'true'; }")
        try:
            button.click(timeout=8000)
            try:
                page.wait_for_function("""() => {
                    const passwords = [...document.querySelectorAll('input[type=password]')].filter(el => el.getClientRects().length);
                    const application = [...document.querySelectorAll('h1,h2,h3')].some(el =>
                        el.getClientRects().length && /^(application|autofill with resume|my information|my experience|application questions)/i.test(el.innerText.trim()));
                    return (!passwords.length && application) || /verify your|verification|check your (email|inbox)|already (exists|registered|in use)|account exists|invalid|incorrect|unable|error/i.test(document.body.innerText);
                }""", timeout=15000)
                return True
            except Exception:
                return False  # A click without any response is not progress.
        finally:
            page.locator('form[data-pocket-account]').evaluate_all("forms => forms.forEach(form => delete form.dataset.pocketAccount)")

    def step(self, page, gate: str) -> AccountStep:
        try:
            return self._step(page, gate)
        except Exception as exc:
            # Exception messages can contain a fill value. Report only a stage and type.
            return AccountStep(message=f"Workday stopped while {self.stage} ({type(exc).__name__}). No automatic retry was sent.")

    def _step(self, page, gate: str) -> AccountStep:
        self.stage = "reading the account page"
        if not self.scope or account_scope(page.url) != self.scope:
            return AccountStep(message="Account automation stopped: this page is outside the original Workday career site.")
        if gate not in {"start", "login"}:
            return AccountStep()
        # Workday's cookie notice can cover the account controls.
        decline = _visible(page.locator('[data-automation-id="legalNoticeDeclineButton"]'))
        if len(decline) == 1:
            decline[0].click(timeout=8000)
        if not self.email:
            return AccountStep(message="No email address was found in your resume or personal profile. Add it to either source before creating an account.")
        # Verification is completed by the user in the browser.
        body = page.locator("body").inner_text().casefold()
        if re.search(r"verify your (?:email|account)|verification (?:code|email)|check your (?:email|inbox)|one.time (?:code|password)|two.factor", body):
            return AccountStep(message="Complete Workday email verification or two-factor authentication in the browser, then continue here.")
        if gate == "start":
            for name in ("Apply Manually", "Apply", "Apply Now"):
                buttons = _visible(page.get_by_role("button", name=name, exact=True)) + _visible(page.get_by_role("link", name=name, exact=True))
                key = (page.url, name)
                if len(buttons) == 1 and key not in self.attempts:
                    self.attempts.add(key)
                    buttons[0].click(timeout=8000)
                    page.wait_for_timeout(750)
                    return AccountStep(True, "Opened the Workday application.")
            return AccountStep(message="Open Apply Manually in the browser to continue.")
        if re.search(r"already (?:exists|registered)|account (?:already exists|exists)|email.*already (?:in use|used)", body):
            self.recover_existing = True
        password_fields = _visible(page.locator('input[type="password"]'))
        # If no password field is shown, open the sign-in dialog rather than touching application fields.
        if not password_fields:
            return self._open(page, "Sign In")
        try:
            saved = self.credentials.get(self.scope, self.email)
            pending = self.credentials.is_pending(self.scope, self.email)
        except Exception:
            return AccountStep(message="Could not read macOS Keychain. Unlock it and retry; no password was entered.")
        creating = len(password_fields) == 2
        if self.recover_existing and self.generated_password:
            saved = None  # A failed signup's generated password is not the existing account password.
        if len(password_fields) > 2:
            return AccountStep(message="Unrecognized Workday password form; complete it in the browser.")
        if creating and self.recover_existing:
            return self._open(page, "Sign In")
        if creating and saved and not pending:
            return self._open(page, "Sign In")
        if not creating and not saved and not self.recover_existing:
            opened = self._open(page, "Create Account", "Create an Account")
            if opened.progressed:
                return opened
            return AccountStep(message="Could not identify Workday's Create Account control. Open it in the browser.")
        action = "Create Account" if creating else "Sign In"
        key = (self.scope, action)
        if key in self.attempts:
            return AccountStep(message="Workday did not finish authentication. Check the browser for errors, verification, or account recovery; automatic retries stopped.")
        emails = _visible(page.locator('input[type="email"], input[data-automation-id="email"]'))
        usernames = _visible(page.locator('input[name="username"], input[data-automation-id="username"], input[autocomplete="username"]'))
        if not emails:
            emails = usernames
            usernames = []
        automation = "createAccountSubmitButton" if creating else "signInSubmitButton"
        buttons = _visible(page.locator(f'[data-automation-id="{automation}"]'))
        if not buttons:
            buttons = _visible(page.get_by_role("button", name=action, exact=True))
        if creating and not buttons:
            buttons = _visible(page.get_by_role("button", name="Create an Account", exact=True))
        if len(emails) != 1 or len(buttons) != 1:
            return AccountStep(message="Unrecognized Workday account form. Complete authentication in the browser.")
        # The user authorized Workday registration consent. Limit automatic checking
        # to Workday's specific account checkbox, never general application attestations.
        account_consent = []
        for box in _visible(page.locator('input[type="checkbox"]')):
            if box.is_checked():
                continue
            if creating and box.get_attribute("data-automation-id") == "createAccountCheckbox":
                account_consent.append(box)
                continue
            terms = box.evaluate("""el => el.required || el.getAttribute('data-automation-id') === 'createAccountCheckbox' || /agree|accept|consent|privacy|terms/i.test(
                [...(el.labels || [])].map(label => label.innerText).join(' ') + ' ' + (el.getAttribute('aria-label') || ''))""")
            if terms:
                return AccountStep(message="Read and accept Workday's account terms in the browser, then continue.")
        password = saved
        if creating:
            password = saved or self.default_password or new_password()
            self.generated_password = not bool(saved)
        elif self.recover_existing and not saved:
            password = self.password_prompt(f"Existing Workday password for {self.email} (hidden; Enter to handle in browser): ")
        if not password:
            return AccountStep(message="Complete sign-in in the browser.")
        if creating and self.default_password and not saved:
            if ("uppercase character" in body and not any(c.isupper() for c in password)) or (
                "special character" in body and password.isalnum()
            ):
                return AccountStep(message="The configured password does not meet this Workday page's uppercase/special-character requirements. Set POCKET_AGENT_ACCOUNT_PASSWORD to a matching password.")
        try:
            # Save generated credentials BEFORE creating the account, including when verification is required.
            self.credentials.put(self.scope, self.email, password, pending=creating)
        except Exception:
            return AccountStep(message="Could not save credentials in macOS Keychain; no account was created or password entered.")
        self.attempts.add(key)
        self.stage = "entering account credentials"
        self._type_credential(emails[0], self.email)
        for username in usernames:
            if username != emails[0]:
                self._type_credential(username, self.email)
        for field in password_fields:
            self._type_credential(field, password)
        for box in account_consent:
            self.stage = "checking Workday registration consent"
            # Workday draws a decorative layer over the native input. Keyboard
            # activation avoids that layer intercepting Playwright's check().
            box.focus()
            box.press("Space", timeout=8000)
            if not box.is_checked():
                return AccountStep(message="Workday's registration consent did not stay checked. No account request was sent.")
        self.stage = "sending the account request"
        if not self._click(page, buttons[0]):
            return AccountStep(message="Clicked the Workday account control, but the page did not advance or show an error. Account creation is not confirmed; no duplicate request will be sent.")
        response_text = page.locator("body").inner_text().casefold()
        if re.search(r"invalid username\s*/\s*password|invalid (?:username|password)|incorrect password", response_text):
            return AccountStep(message="Workday rejected the account request: Invalid Username/Password. The control was activated, but account creation did not succeed. Check the account email/password or use an existing account; no automatic retry was sent.")
        if creating:
            from pocket_agent.forms.reader import extract_page
            if extract_page(page).gate not in {"login", "verification", "captcha"}:
                self.credentials.put(self.scope, self.email, password, pending=False)
        return AccountStep(True, "Sent the Workday account request; credentials are stored in macOS Keychain.")

    @staticmethod
    def _type_credential(field, value: str) -> None:
        # Workday's register handler needs keyboard/blur validation, not just
        # the input event from fill(). Without these the visible button can do nothing.
        field.fill("")
        field.press_sequentially(value, delay=15)
        field.press("Tab")

    def _open(self, page, *names: str) -> AccountStep:
        for name in names:
            key = (self.scope or "", "open:" + name)
            if key in self.attempts:
                continue
            # Tabs/links can coexist with the actual form's submit button.
            automation = "signInLink" if name == "Sign In" else "createAccountLink"
            controls = _visible(page.locator(f'[data-automation-id="{automation}"]'))
            if not controls:
                controls = _visible(page.get_by_role("link", name=name, exact=True)) + _visible(page.get_by_role("tab", name=name, exact=True))
            if not controls:
                controls = _visible(page.get_by_role("button", name=name, exact=True))
            if len(controls) == 1:
                self.attempts.add(key)
                controls[0].click(timeout=8000)
                page.wait_for_timeout(500)
                return AccountStep(True, "Opened Workday " + name + ".")
        return AccountStep()
