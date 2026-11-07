"""Build form memory from a profile.yaml dict. Fictional data only."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

import yaml

from pocket_agent.forms.memory import Memory
from pocket_agent.profile import example_profile_path

# Answers the eval's scripted user gives. always_use is the explicit opt-in.
SCRIPTED_ANSWERS: dict[str, tuple[str, bool]] = {
    "postal_code": ("78701", False),
    "address_line": ("100 Congress Avenue", False),
    "how_did_you_hear": ("Referral", False),
    "referrer_name": ("Sam Rivera", False),
    "hispanic": ("No", False),
    "export_control": ("No", False),
    "gov_employment": ("No", False),
    "gov_executive": ("No", False),
    "gov_cabinet": ("No", False),
    "gov_contractor": ("No", False),
    "gov_relative": ("No", False),
    "available_start": ("2026-06-15", False),
    "spoken_language": ("English, native", False),
    "interview_language": ("Python", False),
    "onsite": ("3 days", False),
    "office_choice": ("Austin", False),
    "degree_by_start": ("Yes", False),
    "grad_window": ("Yes", False),
    "cover_letter": ("A short note about reliable software.", False),
    "essay": ("I like shipping small tools that people can trust.", False),
    "pronouns": ("they/them", False),
    "preferred_name": ("", False),
    "sms_consent": ("No", False),
    "certify": ("Yes", False),
    "arbitration": ("Yes", False),
    "signature_date": ("2026-11-04", False),
    "gender": ("Decline to self-identify", True),
    "race": ("Decline to self-identify", True),
    "veteran_status": ("I am not a protected veteran", True),
    "disability_status": ("No, I do not have a disability", True),
    "salary_expectation": ("80000", False),
    "criminal_history": ("No", False),
    "signature": ("Jordan Hale", False),
    "ssn": ("", False),
    "dob": ("1999-04-12", False),
    "government_id": ("", False),
}


def load_example_profile() -> dict[str, Any]:
    return yaml.safe_load(example_profile_path().read_text(encoding="utf-8"))


def eval_profile() -> dict[str, Any]:
    """Example profile plus a second fictional job, so repeaters have two rows."""
    data = deepcopy(load_example_profile())
    data["experience"].append(
        {
            "title": "Campus Library Aide",
            "org": "Northwind University Library",
            "start": "2024-09",
            "end": "2025-04",
            "highlights": ["Shelved course reserves and kept the desk log."],
        }
    )
    data["constraints"]["notes"] = "Fictional eval profile. Not a real person."
    return data


def seed_memory(memory: Memory, profile: dict[str, Any], *, resume_path: str = "") -> None:
    first, _, last = str(profile.get("name") or "").partition(" ")
    _put(memory, "first_name", first)
    _put(memory, "last_name", last)
    _put(memory, "full_name", str(profile.get("name") or ""))
    _put(memory, "email", str(profile.get("email") or ""))
    _put(memory, "phone", str(profile.get("phone") or ""))
    location = profile.get("location") or {}
    personal = profile.get("personal") or {}
    for source_key, target_key in {
        "address_line_1": "address_line", "address_line_2": "address_line_2",
        "postal_code": "postal_code", "county": "county",
    }.items():
        value = location.get(source_key) or personal.get(source_key)
        if value:
            memory.put(target_key, str(value), source="profile")
    _put(memory, "city", str(location.get("city") or ""))
    _put(memory, "region", str(location.get("region") or ""))
    _put(memory, "country", str(location.get("country") or ""))
    _put(memory, "location", ", ".join(str(part) for part in (location.get("city"), location.get("region")) if part))
    _put(memory, "willing_to_relocate", "Yes" if location.get("willing_to_relocate") else "No")
    _put(memory, "remote_ok", "Yes" if location.get("remote_ok") else "No")
    education = (profile.get("education") or [{}])[0]
    _put(memory, "school", str(education.get("school") or ""))
    _put(memory, "degree", str(education.get("degree") or ""))
    _put(memory, "field", str(education.get("field") or ""))
    _put(memory, "graduation", str(education.get("graduation") or ""))
    _put(memory, "gpa", str(education.get("gpa") or ""))
    for index, school in enumerate(profile.get("education") or []):
        _put(memory, f"education.{index}.start", str(school.get("start") or ""))
        _put(memory, f"education.{index}.end", str(school.get("end") or school.get("graduation") or ""))
    for index, job in enumerate(profile.get("experience") or []):
        _put(memory, f"work.{index}.title", str(job.get("title") or ""))
        _put(memory, f"work.{index}.org", str(job.get("org") or ""))
        _put(memory, f"work.{index}.start", str(job.get("start") or ""))
        _put(memory, f"work.{index}.end", str(job.get("end") or ""))
        highlights = job.get("highlights") or []
        _put(memory, f"work.{index}.description", " ".join(str(item) for item in highlights))
    citizenship = profile.get("citizenship") or {}
    places = citizenship.get("authorized_to_work") or []
    _put(memory, "work_authorization", "Yes" if "US" in places else "No")
    residence = str(location.get("country") or "").casefold()
    _put(memory, "located_in_us", "Yes" if residence in {"us", "usa", "united states", "united states of america"} else "No")
    status = "Citizen" if str(citizenship.get("country") or "").upper() == "US" else "Permanent Resident" if "US" in (citizenship.get("permanent_residence") or []) else ""
    _put(memory, "auth_status", status)
    degree = str(education.get("degree") or "")
    _put(memory, "has_bachelors", "Yes" if degree.upper() in {"BS", "BA", "BSC", "BACHELOR", "BACHELOR'S"} or "bachelor" in degree.casefold() else "No")
    jobs = list(profile.get("experience") or [])
    if jobs:
        latest = max(jobs, key=lambda job: str(job.get("end") or ""))
        _put(memory, "most_recent_employer", str(latest.get("org") or ""))
    _put(memory, "requires_sponsorship", "Yes" if citizenship.get("requires_sponsorship") else "No")
    clearance = str(citizenship.get("clearance") or "none")
    _put(memory, "clearance", "No" if clearance.casefold() in {"none", "no", ""} else clearance)
    skills = profile.get("skills") or {}
    flat: list[str] = []
    if isinstance(skills, dict):
        for values in skills.values():
            flat.extend(str(item) for item in values)
    elif isinstance(skills, list):
        flat.extend(str(item) for item in skills)
    _put(memory, "skills", ", ".join(flat))
    links = profile.get("links") or {}
    _put(memory, "linkedin", str(links.get("linkedin") or ""))
    _put(memory, "github", str(links.get("github") or ""))
    if resume_path:
        _put(memory, "resume", resume_path)
    from pocket_agent.forms.policy import is_sensitive_key
    for key, answer in (profile.get("form_answers") or {}).items():
        if isinstance(answer, str) and not is_sensitive_key(key):
            _put(memory, key, answer)
        elif isinstance(answer, dict) and answer.get("always_use") is True and answer.get("value"):
            source = "profile"
            if answer.get("label"):
                label = " ".join(answer["label"].replace('*', '').split()).strip().casefold()
                source = "profile_exact:" + hashlib.sha256(label.encode()).hexdigest()
            memory.put(key, str(answer["value"]), source=source, sensitive=is_sensitive_key(key), always_use=True, label=answer.get("label"))


def _put(memory: Memory, key: str, value: str) -> None:
    if not value.strip():
        return
    memory.put(key, value, source="resume", sensitive=False, always_use=False)
