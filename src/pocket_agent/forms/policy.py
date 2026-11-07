"""Sensitive-field policy. These are asked, never guessed."""

from __future__ import annotations

import re

# (pattern, canonical key). First match wins. Patterns are general, not form ids.
SENSITIVE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnon[ -]?compete\b", re.I), "non_compete"),
    (re.compile(r"\bfamily members?\b.*\b(?:work|employed)\b.*\bzebra\b", re.I), "company.zebra.family_members"),
    (re.compile(r"\bpermanent residen(?:ce|cy) or citizenship\b.*\banother country\b", re.I), "other_residency_or_citizenship"),
    (re.compile(r"\b(gender|sex)\b", re.I), "gender"),
    (re.compile(r"\b(hispanic|latino)\b", re.I), "hispanic"),
    (re.compile(r"\b(race|ethnicity)\b", re.I), "race"),
    (re.compile(r"veteran", re.I), "veteran_status"),
    (re.compile(r"disabilit", re.I), "disability_status"),
    (re.compile(r"\b(salary|compensation|pay expectations?|desired pay|expected pay|wage)\b", re.I), "salary_expectation"),
    (re.compile(r"\b(felony|felonies|criminal|conviction|convicted)\b", re.I), "criminal_history"),
    (re.compile(r"arbitration", re.I), "arbitration"),
    (re.compile(r"\b(e-signature|electronic signature|signature|sign your name)\b", re.I), "signature"),
    (re.compile(r"\b(certify|certification)\b", re.I), "certify"),
    (re.compile(r"\b(ssn|social security)\b", re.I), "ssn"),
    (re.compile(r"\b(date of birth|birth date|birthdate|\bdob\b)\b", re.I), "dob"),
    (re.compile(r"\b(passport|driver'?s license|government id|national id)\b", re.I), "government_id"),
    (re.compile(r"\b(export control|protected individual|1324b|following countries|country other than|citizenship in any country)\b", re.I), "export_control"),
    (re.compile(r"\b(cabinet|congress)\b", re.I), "gov_cabinet"),
    (re.compile(r"executive[- ]branch", re.I), "gov_executive"),
    (re.compile(r"\b(government contractor|procurement official)\b", re.I), "gov_contractor"),
    (re.compile(r"\b(relatives employed|family members? employed)\b", re.I), "gov_relative"),
    (re.compile(r"\b(federal|state or local|public institution|government agency)\b", re.I), "gov_employment"),
    (re.compile(r"\b(spoken language|languages you speak|language proficiency)\b", re.I), "spoken_language"),
    (re.compile(r"\b(programming language|coding language|technical interview)\b", re.I), "interview_language"),
    (re.compile(r"\b(on-site|onsite|hybrid|(days|times) (a|per) week|from our ([\w-]+ ){0,3}office|work from the office)\b", re.I), "onsite"),
    (re.compile(r"\b(which office|office location|preferred office)\b", re.I), "office_choice"),
    (re.compile(r"\b(will you have|completed your degree|before your|by your start|anticipated start)\b", re.I), "degree_by_start"),
    (re.compile(r"\b(graduation window|required window|fall between)\b", re.I), "grad_window"),
    (re.compile(r"\b(cover letter|additional information|additional info)\b", re.I), "cover_letter"),
    (re.compile(r"^(why\b)|what excites|engineering work", re.I), "essay"),
    (re.compile(r"\bpronouns?\b", re.I), "pronouns"),
    (re.compile(r"\bpreferred name\b", re.I), "preferred_name"),
    (re.compile(r"\b(sms|text messages?)\b", re.I), "sms_consent"),
)

SENSITIVE_KEYS = {key for _, key in SENSITIVE_RULES}


def sensitive_key(label: str) -> str | None:
    for pattern, key in SENSITIVE_RULES:
        if pattern.search(label):
            return key
    return None


def is_sensitive_key(key: str | None) -> bool:
    return bool(key) and key in SENSITIVE_KEYS


def ask_key(label: str, section: str = "") -> str | None:
    """Key that must be asked, not guessed. Work-history dates are not in this set."""
    forced = sensitive_key(label)
    if forced:
        return forced
    if section == "eeo" and re.search(r"\bdate\b", label, re.I) and "birth" not in label.casefold():
        return "signature_date"
    if re.search(
        r"\b(earliest start|available to start|when can you start|date you can (start|begin)|date you are available)\b",
        label,
        re.I,
    ):
        return "available_start"
    return None


def question_for(label: str) -> str:
    cleaned = label.strip().rstrip("?").rstrip("*").strip()
    return f"What should I put for \"{cleaned}\"?"
