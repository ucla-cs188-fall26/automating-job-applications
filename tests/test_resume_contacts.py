import pytest
from pocket_agent.resume import resume_contact_profile


def test_resume_contacts_override_sample_profile(tmp_path):
    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nalex@personal.test | +1-415-555-1234\nhttps://github.com/alex\n')
    original = {'name': 'Jordan Hale', 'email': 'jordan.hale@example.com'}
    result = resume_contact_profile(original, resume)
    assert result['email'] == 'alex@personal.test'
    assert result['name'] == 'Alex Rivera'
    assert result['phone'] == '+1-415-555-1234'
    assert original['name'] == 'Jordan Hale'


def test_missing_resume_email_falls_back_to_profile(tmp_path):
    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nSoftware Engineer')
    assert resume_contact_profile({'email': 'profile@personal.test'}, resume)['email'] == 'profile@personal.test'


def test_multiple_resume_emails_are_ambiguous(tmp_path):
    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nalex@personal.test other@personal.test')
    with pytest.raises(ValueError, match='multiple email'):
        resume_contact_profile({}, resume)


def test_resume_first_then_personal_profile_fallback(tmp_path):
    from pocket_agent.forms.memory import Memory
    from pocket_agent.forms.seed import seed_memory
    from pocket_agent.forms.match import match_label

    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nalex@resume.test\nEducation\n')
    original = {
        'email': 'legacy@personal.test',
        'personal': {'email': 'personal@personal.test', 'phone': '+1-415-555-0101',
                     'address_line_1': '123 Example Road', 'address_line_2': 'Apt 4',
                     'postal_code': '12345', 'county': 'Example County', 'city': 'Example City'},
    }
    merged = resume_contact_profile(original, resume)
    assert merged['email'] == 'alex@resume.test'
    assert merged['phone'] == '+1-415-555-0101'
    memory = Memory(tmp_path / 'profile.sqlite')
    try:
        seed_memory(memory, merged)
        for label, expected in [('Address Line 1', '123 Example Road'), ('Address Line 2', 'Apt 4'), ('Postal Code', '12345'), ('County', 'Example County'), ('City', 'Example City')]:
            match = match_label(label)
            assert match is not None, label
            assert memory.get(match.key).value == expected, label
    finally:
        memory.close()
    assert original['email'] == 'legacy@personal.test'


def test_missing_resume_email_uses_personal_email_before_legacy_profile(tmp_path):
    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nEducation\n')
    merged = resume_contact_profile({'email': 'legacy@personal.test', 'personal': {'email': 'preferred@personal.test'}}, resume)
    assert merged['email'] == 'preferred@personal.test'


def test_personal_address_fills_required_browser_fields_without_asking(tmp_path):
    from playwright.sync_api import sync_playwright
    from pocket_agent.forms.engine import fill_form
    from pocket_agent.forms.memory import Memory
    from pocket_agent.forms.seed import seed_memory

    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nalex@resume.test\n')
    merged = resume_contact_profile({'personal': {'address_line_1': '123 Example Road', 'postal_code': '12345', 'county': 'Example County'}}, resume)
    memory = Memory(tmp_path / 'browser.sqlite')
    seed_memory(memory, merged)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='<label>Address Line 1<input required></label><label>Postal Code<input required></label><label>County<input required></label><button type=submit>Submit Application</button>'))
            result = fill_form(page, 'https://example.test', memory, None, lambda _: pytest.fail('Profile fallback should resolve required fields'))
            assert page.locator('input').evaluate_all('els=>els.map(el=>el.value)') == ['123 Example Road', '12345', 'Example County']
            assert result.submits == 0
            browser.close()
    finally:
        memory.close()
