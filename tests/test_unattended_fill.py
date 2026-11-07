import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pocket_agent.cli import cmd_fill
from pocket_agent.forms.engine import Asked, FormRun
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.seed import seed_memory, eval_profile


@pytest.mark.parametrize('gate', ['', 'login', 'captcha'])
@pytest.mark.parametrize('mode', [True, None])
def test_unattended_never_requests_terminal_input(home: Path, monkeypatch, gate, mode):
    (home / 'profile.yaml').write_text(Path('examples/profile.yaml').read_text())
    (home / 'config.yaml').write_text('fill:\n  unattended: true\n')
    monkeypatch.setattr('builtins.input', lambda *_: pytest.fail('Unattended mode must not call input'))
    class Page:
        def wait_for_event(self, event, timeout):
            assert event == 'close' and timeout == 0
    class Browser:
        def new_page(self): return Page()
        def close(self): pass
    class Playwright:
        chromium = SimpleNamespace(launch=lambda **_: Browser())
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: Playwright())
    def fill(page, url, memory, resume_file, ask, decider, **kwargs):
        answer = ask(Asked('unknown', 'Unknown application question', 'Question?'))
        assert answer.skip is True
        return FormRun(url=url, gate=gate)
    monkeypatch.setattr('pocket_agent.forms.engine.fill_form', fill)
    args = SimpleNamespace(url='https://example.test/apply', model='none', resume=None, workday_accounts=False, unattended=mode)
    assert cmd_fill(args) == 2
    report = json.loads((home / 'last-fill-report.json').read_text())
    assert report['unresolved'] == ['Unknown application question']
    assert report['gate'] == gate


def test_profile_form_answers_seed_only_explicit_sensitive_preferences(tmp_path):
    profile = eval_profile()
    profile['form_answers'] = {
        'how_did_you_hear': 'Simplify',
        'salary_expectation': '90000',
        'veteran_status': {'value': 'Decline to answer', 'always_use': True},
    }
    memory = Memory(tmp_path / 'answers.sqlite')
    try:
        seed_memory(memory, profile)
        assert memory.get('how_did_you_hear').value == 'Simplify'
        assert memory.get('salary_expectation') is None
        assert memory.get('veteran_status').always_use is True
    finally:
        memory.close()


def test_unattended_skip_does_not_erase_saved_sensitive_answer(tmp_path):
    from playwright.sync_api import sync_playwright
    from pocket_agent.forms.engine import fill_form, UserAnswer

    memory = Memory(tmp_path / 'skip.sqlite')
    memory.put('salary_expectation', '90000', source='answer', sensitive=True, always_use=False)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='<label>Salary expectation<input required></label><button type=submit>Submit application</button>'))
            result = fill_form(page, 'https://example.test', memory, None, lambda _: UserAnswer('', skip=True))
            assert memory.get('salary_expectation').value == '90000'
            assert page.locator('input').input_value() == ''
            assert result.outcomes[0].action == 'skip'
            assert not result.outcomes[0].asked
            assert result.submits == 0
            browser.close()
    finally:
        memory.close()
