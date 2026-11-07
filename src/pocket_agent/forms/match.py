"""Map a field label to a canonical memory key.

Exact phrases and token rules are ordinary application wording. A fuzzy
match is used only when it is clearly closer than the runner-up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from pocket_agent.forms.policy import sensitive_key

_PUNCT = re.compile(r"[^a-z0-9]+")
_STOP = {
    "a", "an", "the", "your", "you", "my", "our", "please", "enter", "provide",
    "type", "input", "of", "for", "and", "or", "to", "in", "on", "at", "any",
    "applicant", "candidate", "this", "application", "required", "optional",
    "field", "information", "info", "contact", "currently", "legally",
    "do", "does", "did", "will", "would", "are", "is", "be", "been",
    "what", "which", "who", "if", "from", "with", "that", "these", "those",
}
_MODIFIERS = {
    "number", "address", "url", "line", "code", "current", "mailing", "home", "upload", "attach", "copy",
    "most", "recent", "able", "hold", "have", "has", "highest", "awarded", "attended", "select", "choose",
    "overall", "tools", "opening", "conferred", "protected", "status",
    "permanent", "street", "postal", "residence", "residential", "mobile",
    "primary", "preferred", "full", "job", "position", "employer", "employment",
    "work", "date", "phone", "earned", "study", "point", "average", "grade",
    "annual", "usd", "name", "us", "united", "states", "future", "now",
    "previous", "prior", "list", "technical", "profile", "link", "page",
}

# phrase (already normalized) -> key
PHRASES: dict[str, str] = {
    "first name": "first_name",
    "given name": "first_name",
    "forename": "first_name",
    "last name": "last_name",
    "family name": "last_name",
    "surname": "last_name",
    "full name": "full_name",
    "legal name": "full_name",
    "email": "email",
    "email address": "email",
    "e mail": "email",
    "phone": "phone",
    "phone number": "phone",
    "mobile": "phone",
    "mobile phone": "phone",
    "mobile telephone": "phone",
    "cell": "phone",
    "cell phone": "phone",
    "telephone": "phone",
    "telephone number": "phone",
    "city": "city",
    "town": "city",
    "location city": "city",
    "current location city": "city",
    "state": "region",
    "province": "region",
    "state province": "region",
    "region": "region",
    "country": "country",
    "location": "location",
    "current location": "location",
    "zip": "postal_code",
    "zip code": "postal_code",
    "postal code": "postal_code",
    "postcode": "postal_code",
    "street address": "address_line",
    "address line": "address_line",
    "mailing address": "address_line",
    "linkedin": "linkedin",
    "linkedin url": "linkedin",
    "linkedin profile": "linkedin",
    "github": "github",
    "github url": "github",
    "github profile": "github",
    "website": "website",
    "portfolio": "website",
    "portfolio url": "website",
    "school": "school",
    "university": "school",
    "college": "school",
    "institution": "school",
    "school name": "school",
    "degree": "degree",
    "degree earned": "degree",
    "highest degree": "degree",
    "field of study": "field",
    "major": "field",
    "concentration": "field",
    "area of study": "field",
    "discipline": "field",
    "academic discipline": "field",
    "gpa": "gpa",
    "grade point average": "gpa",
    "cumulative gpa": "gpa",
    "graduation date": "graduation",
    "date of graduation": "graduation",
    "graduation": "graduation",
    "job title": "work.title",
    "position title": "work.title",
    "position": "work.title",
    "role": "work.title",
    "role title": "work.title",
    "company": "work.org",
    "company name": "work.org",
    "employer": "work.org",
    "employer name": "work.org",
    "organization": "work.org",
    "organisation": "work.org",
    "start date": "work.start",
    "employment start": "work.start",
    "end date": "work.end",
    "employment end": "work.end",
    "responsibilities": "work.description",
    "job description": "work.description",
    "role description": "work.description",
    "work authorization": "work_authorization",
    "authorized to work": "work_authorization",
    "require sponsorship": "requires_sponsorship",
    "visa sponsorship": "requires_sponsorship",
    "sponsorship": "requires_sponsorship",
    "willing to relocate": "willing_to_relocate",
    "open to relocation": "willing_to_relocate",
    "relocation": "willing_to_relocate",
    "work remotely": "remote_ok",
    "remote work": "remote_ok",
    "security clearance": "clearance",
    "clearance": "clearance",
    "skills": "skills",
    "technical skills": "skills",
    "how did you hear": "how_did_you_hear",
    "hear about": "how_did_you_hear",
    "referral source": "how_did_you_hear",
    "resume": "resume",
    "cv": "resume",
    "resume cv": "resume",
    "upload resume": "resume",
    "highest level of education": "degree",
    "level of education": "degree",
    "most recent employer": "most_recent_employer",
    "most recent company": "most_recent_employer",
    "most recent organization": "most_recent_employer",
    "personal website": "website",
    "employment authorization": "auth_status",
    "authorization status": "auth_status",
    "authorization category": "auth_status",
    "referrer name": "referrer_name",
    "referrer": "referrer_name",
}

_RAW_PHRASES = PHRASES
PHRASES = {}  # filled once phrase() exists

# All of these tokens present -> key. Used when the wording is a full question.
CONTAINS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("authorized", "work"), "work_authorization"),
    (("sponsorship",), "requires_sponsorship"),
    (("relocate",), "willing_to_relocate"),
    (("relocation",), "willing_to_relocate"),
    (("clearance",), "clearance"),
    (("remotely",), "remote_ok"),
    (("remote",), "remote_ok"),
    (("graduation",), "graduation"),
    (("portfolio",), "website"),
    (("website",), "website"),
    (("referrer",), "referrer_name"),
    (("previously", "employed"), "previously_employed_here"),
    (("previously", "worked"), "previously_employed_here"),
    (("ever", "worked"), "previously_employed_here"),
    (("current", "employer"), "most_recent_employer"),
    (("worked", "company"), "previously_employed_here"),
    (("worked", "employer"), "previously_employed_here"),
    (("employed", "employer"), "previously_employed_here"),
    (("applied", "employer"), "previously_employed_here"),
    (("applied", "company"), "previously_employed_here"),
    (("located", "us"), "located_in_us"),
    (("located", "united"), "located_in_us"),
    (("learn", "about"), "how_did_you_hear"),
    (("learn", "opening"), "how_did_you_hear"),
    (("learn", "role"), "how_did_you_hear"),
    (("bachelor",), "has_bachelors"),
    (("school",), "school"),
    (("university",), "school"),
    (("title",), "work.title"),
    (("first", "name"), "first_name"),
    (("given", "name"), "first_name"),
    (("last", "name"), "last_name"),
    (("family", "name"), "last_name"),
    (("most", "recent"), "most_recent_employer"),
    (("github",), "github"),
    (("linkedin",), "linkedin"),
    (("gpa",), "gpa"),
    (("hear", "about"), "how_did_you_hear"),
    (("how", "hear"), "how_did_you_hear"),
    (("referral", "source"), "how_did_you_hear"),
    (("postal",), "postal_code"),
    (("zip",), "postal_code"),
    (("postcode",), "postal_code"),
)

_SECTION = {
    "work.title": "work",
    "work.org": "work",
    "work.start": "work",
    "work.end": "work",
    "work.description": "work",
    "school": "education",
    "degree": "education",
    "field": "education",
    "gpa": "education",
    "graduation": "education",
    "education.start": "education",
}

FUZZY_MIN = 0.93


@dataclass(frozen=True)
class Match:
    key: str
    score: float
    reason: str


def tokens(text: str) -> list[str]:
    raw = text.casefold().replace("'s", " ").replace("’s", " ")
    raw = _PUNCT.sub(" ", raw)
    return [part for part in raw.split() if part not in _STOP]


def phrase(text: str) -> str:
    return " ".join(tokens(text))


PHRASES = {phrase(text): key for text, key in _RAW_PHRASES.items()}


def section_of(heading: str) -> str:
    text = heading.casefold()
    if any(word in text for word in ("equal", "voluntary", "self-ident", "self ident", "demographic", "eeo")):
        return "eeo"
    if any(word in text for word in ("experience", "employment", "work history")):
        return "work"
    if "question" in text:
        return ""
    if "review" in text:
        return "review"
    if any(word in text for word in ("voluntary", "self-identify", "self identify", "ofccp", "disability")):
        return "eeo"
    if "education" in text or "academic" in text:
        return "education"
    if any(word in text for word in ("contact", "personal", "my information", "profile")):
        return "contact"
    return ""


def _section_ok(key: str, section: str) -> bool:
    expected = _SECTION.get(key)
    if expected is None or section in {"", "contact", "eeo"}:
        return True
    return expected == section


def _subset_match(label_tokens: list[str]) -> str | None:
    label_set = set(label_tokens)
    best: tuple[int, str] | None = None
    for synonym, key in PHRASES.items():
        parts = synonym.split()
        if not parts or not set(parts) <= label_set:
            continue
        extra = label_set - set(parts)
        if extra and not extra <= _MODIFIERS:
            continue
        rank = len(parts)
        if best is None or rank > best[0]:
            best = (rank, key)
    return None if best is None else best[1]


def _contains_match(label_tokens: list[str]) -> str | None:
    present = set(label_tokens)
    found: list[str] = []
    for needles, key in CONTAINS:
        if set(needles) <= present and key not in found:
            found.append(key)
    if len(found) == 1:
        return found[0]
    return None


def _fuzzy(text: str, section: str) -> Match | None:
    scored: list[tuple[float, str]] = []
    for synonym, key in PHRASES.items():
        if not _section_ok(key, section):
            continue
        ratio = SequenceMatcher(None, text, synonym).ratio()
        if ratio >= FUZZY_MIN:
            scored.append((ratio, key))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_score, best_key = scored[0]
    keys = {key for score, key in scored if score >= best_score - 0.02}
    if len(keys) != 1:
        return None
    return Match(best_key, best_score, "fuzzy")


_DATE_PART = re.compile(r"^(start|end) date (year|month)$")


def date_part_key(label: str, section: str) -> str | None:
    """Key for a split date ("Start date year") once the block is known to be work or education."""
    found = _DATE_PART.match(phrase(label))
    if not found:
        return None
    which, part = found.groups()
    if section == "work":
        return f"work.{which}"
    if section == "education":
        return "graduation" if which == "end" else "education.start"
    return None


def match_label(label: str, *, section: str = "", learned: dict[str, str] | None = None) -> Match | None:
    """Return the canonical key for a label, or None when it is ambiguous."""
    sensitive = sensitive_key(label)
    if sensitive:
        return Match(sensitive, 1.0, "sensitive")
    text = phrase(label)
    if not text:
        return None
    dated = date_part_key(label, section)
    if dated:
        return Match(dated, 1.0, "exact")
    if learned:
        # Memory stores labels casefolded with punctuation; compare them the same way as the label.
        key = learned.get(text) or {phrase(seen): saved for seen, saved in learned.items()}.get(text)
        # "unknown" was once a shared slot for every unmatched question. Its answer belongs to no label.
        if key and key != "unknown" and _section_ok(key, section):
            return Match(key, 1.0, "learned")
    if text in PHRASES and _section_ok(PHRASES[text], section):
        return Match(PHRASES[text], 1.0, "exact")
    parts = text.split()
    contained = _contains_match(parts)
    if contained and _section_ok(contained, section):
        return Match(contained, 1.0, "tokens")
    subset = _subset_match(parts)
    if subset and _section_ok(subset, section):
        return Match(subset, 1.0, "subset")
    return _fuzzy(text, section)


def suggestions(label: str, section: str = "", limit: int = 5) -> list[tuple[str, float]]:
    """Loose candidates for the constrained model. Not used for direct fills."""
    text = phrase(label)
    best: dict[str, float] = {}
    for synonym, key in PHRASES.items():
        if not _section_ok(key, section):
            continue
        ratio = SequenceMatcher(None, text, synonym).ratio()
        if ratio > best.get(key, 0):
            best[key] = ratio
    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def indexed_key(base: str, group: int) -> str:
    if base == "graduation" and group:
        return f"education.{group}.end"
    if base.startswith("work."):
        field = base.split(".", 1)[1]
        return f"work.{group}.{field}"
    if base.startswith("education."):
        field = base.split(".", 1)[1]
        return f"education.{group}.{field}"
    return base
