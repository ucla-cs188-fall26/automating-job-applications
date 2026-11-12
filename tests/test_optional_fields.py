import pytest
from playwright.sync_api import sync_playwright
from pocket_agent.forms.engine import fill_form, UserAnswer
from pocket_agent.forms.memory import Memory


@pytest.mark.parametrize('label', [
    'I have a preferred name',
    'Do you have a preferred name?',
    'I use a different name',
    'I have another name',
    'I have a chosen name',
    'Send me SMS text messages',
    'I have a middle name',
])
def test_optional_checkbox_variants_do_not_prompt(tmp_path, label):
    memory = Memory(tmp_path / 'optional.sqlite')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=f'<label><input type=checkbox>{label}</label><button type=submit>Submit application</button>'))
            result = fill_form(page, 'https://example.test', memory, None, lambda _: pytest.fail('Optional preference must not prompt'))
            assert result.error is None
            assert not page.locator('input').is_checked()
            assert result.outcomes[0].action == 'skip'
            assert not result.outcomes[0].asked
            assert result.submits == 0
            browser.close()
    finally:
        memory.close()


def test_optional_saved_preference_still_applies(tmp_path):
    memory = Memory(tmp_path / 'preference.sqlite')
    memory.put('preferred_name', 'Yes', always_use=True, source='answer')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='<label><input type=checkbox>I have a preferred name</label><button type=submit>Submit application</button>'))
            result = fill_form(page, 'https://example.test', memory, None, lambda _: pytest.fail('Saved answer should be reused'))
            assert page.locator('input').is_checked()
            assert result.outcomes[0].action == 'fill'
            browser.close()
    finally:
        memory.close()


def test_optional_existing_text_is_preserved(tmp_path):
    memory = Memory(tmp_path / 'existing.sqlite')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='<label>Preferred name<input value="Alex"></label><label>Other detail<input value="Resume-provided detail"></label><button type=submit>Submit application</button>'))
            result = fill_form(page, 'https://example.test', memory, None, lambda _: pytest.fail('Optional fields must not prompt'))
            assert page.locator('input').evaluate_all('els => els.map(el => el.value)') == ['Alex', 'Resume-provided detail']
            assert result.submits == 0
            browser.close()
    finally:
        memory.close()
