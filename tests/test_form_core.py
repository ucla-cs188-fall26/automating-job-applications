"""Matcher, policy, memory, and catalog invariants. No browser."""

from __future__ import annotations

from pocket_agent.forms.catalog import FORMS, forms_for
from pocket_agent.forms.guard import classify_button, progress_is_last
from pocket_agent.forms.match import PHRASES, _RAW_PHRASES, match_label, phrase
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import sensitive_key
from pocket_agent.forms.values import equivalent, present_value


def test_phrase_table_does_not_collapse_two_keys(tmp_path) -> None:
    folded: dict[str, str] = {}
    for text, key in _RAW_PHRASES.items():
        normal = phrase(text)
        assert folded.get(normal, key) == key, normal
        folded[normal] = key
    assert PHRASES["given name"] == "first_name"
    assert PHRASES["role title"] == "work.title"


def test_sensitive_labels_are_never_a_guess() -> None:
    assert sensitive_key("What are your pay expectations?") == "salary_expectation"
    assert sensitive_key("Type your full legal name as a signature") == "signature"
    assert sensitive_key("Date of birth") == "dob"
    assert sensitive_key("Have you been convicted of a felony?") == "criminal_history"
    assert sensitive_key("Passport or government ID number") == "government_id"
    assert match_label("Applicant gender").key == "gender"


def test_catalog_labels_resolve_when_they_should_be_filled() -> None:
    misses = []
    for form in FORMS:
        for step in form.steps:
            from pocket_agent.forms.match import section_of

            section = section_of(step.heading)
            for field in step.fields:
                if field.expect != "fill":
                    continue
                found = match_label(field.label, section=section)
                if found is None or found.key != field.key:
                    misses.append((form.id, field.label, field.key, None if found is None else found.key))
    assert misses == []


def test_held_out_labels_are_not_copies_of_dev() -> None:
    dev = {field.label.casefold() for form in forms_for("dev") for step in form.steps for field in step.fields}
    held = {field.label.casefold() for form in forms_for("heldout") for step in form.steps for field in step.fields}
    assert not (dev & held)


def test_catalog_shape() -> None:
    assert len(FORMS) >= 25
    workday = [form for form in FORMS if form.style == "workday"]
    assert len(workday) >= 12
    assert len(forms_for("dev")) >= 10
    assert len(forms_for("heldout")) >= 10
    traps = [form.id for form in FORMS if form.trap]
    assert traps == ["trap-next", "trap-auto"]


def test_guard_blocks_submit_even_when_the_label_says_next() -> None:
    assert classify_button("Next", button_type="submit") == "submit"
    assert classify_button("Next", button_type="button", onclick="document.forms[0].submit()") == "submit"
    assert classify_button("Submit application", button_type="submit") == "submit"
    assert classify_button("Next", button_type="button") == "forward"
    assert classify_button("No thanks", button_type="button") == "dismiss"
    assert classify_button("Add another", button_type="button") == "add"
    assert progress_is_last("Step 2 of 2") is True
    assert progress_is_last("Step 1 of 3") is False
    assert progress_is_last("Application") is False


def test_memory_reuse_skips_sensitive_until_always_use(tmp_path) -> None:
    memory = Memory(tmp_path / "mem.sqlite")
    memory.put("email", "jordan.hale@example.com", source="resume")
    memory.put("gender", "Decline to self-identify", sensitive=True, always_use=False, source="answer")
    assert memory.reusable_keys() == {"email"}
    memory.put("gender", "Decline to self-identify", sensitive=True, always_use=True, source="answer")
    assert "gender" in memory.reusable_keys()
    memory.close()


def test_option_and_date_formatting() -> None:
    assert present_value("BS", kind="select", options=["High school", "Bachelor's", "PhD"]) == "Bachelor's"
    assert present_value("TX", kind="combobox", options=["California", "Texas"]) == "Texas"
    assert present_value("US", kind="select", options=["Canada", "United States"]) == "United States"
    assert present_value("2025-05", kind="date", options=[], placeholder="MM/YYYY") == "05/2025"
    assert equivalent("05/2025", "May 2025")
    assert equivalent("+1-555-0100", "5550100") or equivalent("+1-555-0100", "+1-555-0100")


def test_profile_education_dates_are_scoped_and_split(tmp_path) -> None:
    from pocket_agent.forms.match import indexed_key
    from pocket_agent.forms.seed import eval_profile, seed_memory
    from pocket_agent.forms.values import date_part

    profile = eval_profile()
    profile["education"][0]["start"] = "2022-09"
    profile["education"].append({"school": "Second school", "start": "2020-08", "graduation": "2022-05"})
    memory = Memory(tmp_path / "dates.sqlite")
    try:
        seed_memory(memory, profile)
        for section, group, label, expected in (
            ("education", 0, "Start date year", "2022"),
            ("education", 0, "Start date month", "September"),
            ("education", 0, "End date year", "2026"),
            ("education", 1, "Start date year", "2020"),
            ("education", 1, "End date month", "May"),
            ("work", 0, "Start date year", "2025"),
            ("work", 1, "Start date year", "2024"),
        ):
            match = match_label(label, section=section)
            entry = memory.get(indexed_key(match.key, group))
            assert entry is not None
            assert date_part(label, entry.value, named=True) == expected
    finally:
        memory.close()


def test_missing_education_start_is_not_inferred(tmp_path) -> None:
    from pocket_agent.forms.match import indexed_key
    from pocket_agent.forms.seed import eval_profile, seed_memory

    memory = Memory(tmp_path / "missing.sqlite")
    try:
        seed_memory(memory, eval_profile())
        match = match_label("Start date year", section="education")
        assert memory.get(indexed_key(match.key, 0)) is None
    finally:
        memory.close()
