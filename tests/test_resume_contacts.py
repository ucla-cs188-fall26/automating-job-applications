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


def test_missing_resume_email_never_uses_profile_email(tmp_path):
    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nSoftware Engineer')
    assert resume_contact_profile({'email': 'wrong@example.com'}, resume)['email'] == ''


def test_multiple_resume_emails_are_ambiguous(tmp_path):
    resume = tmp_path / 'resume.txt'
    resume.write_text('Alex Rivera\nalex@personal.test other@personal.test')
    with pytest.raises(ValueError, match='multiple email'):
        resume_contact_profile({}, resume)
