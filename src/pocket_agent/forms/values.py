"""Turn a stored value into the string a control expects."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_MONTHS = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}
_MONTH_INDEX = {name.casefold(): number for number, name in _MONTHS.items()}

_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California",
    "co": "Colorado", "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia",
    "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
    "mt": "Montana", "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey",
    "nm": "New Mexico", "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio",
    "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah", "vt": "Vermont",
    "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
    "dc": "District of Columbia",
}
_COUNTRIES = {
    "us": "United States", "usa": "United States", "united states": "United States",
    "ca": "Canada", "canada": "Canada",
    "uk": "United Kingdom", "gb": "United Kingdom", "united kingdom": "United Kingdom",
    "mx": "Mexico", "mexico": "Mexico",
}
_DEGREES = {
    "bs": "bachelor", "b.s.": "bachelor", "b.s": "bachelor", "bachelor": "bachelor",
    "bachelor's": "bachelor", "bachelors": "bachelor", "ba": "bachelor", "b.a.": "bachelor",
    "ms": "master", "m.s.": "master", "master": "master", "master's": "master", "masters": "master",
    "phd": "phd", "ph.d.": "phd", "ph.d": "phd", "doctorate": "phd",
    "associate": "associate", "high school": "high school",
}
_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}
_EMPTY = {"", "select", "select one", "choose", "choose one", "—", "-"}
# "Decline to self-identify", "I don't wish to answer", "Prefer not to say" are one answer.
_DECLINE = re.compile(
    r"\b(decline|prefer not|rather not|choose not|do not wish|don'?t wish|do not want to (answer|disclose|say)|don'?t want to (answer|disclose|say)|not wish to (answer|disclose|self))\b",
    re.I,
)


def parse_date(value: str) -> tuple[int, int, int] | None:
    text = value.strip()
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)
        return year, month, day
    match = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if match:
        return int(match.group(2)), int(match.group(1)), 1
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        return int(match.group(3)), int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", text)
    if match and match.group(1).casefold() in _MONTH_INDEX:
        return int(match.group(2)), _MONTH_INDEX[match.group(1).casefold()], 1
    return None


def format_date(value: str, *, hint: str = "", input_type: str = "text") -> str:
    parsed = parse_date(value)
    if parsed is None:
        return value
    year, month, day = parsed
    if input_type == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    folded = hint.casefold()
    if "yyyy-mm-dd" in folded or input_type == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if "mm/dd/yyyy" in folded:
        return f"{month:02d}/{day:02d}/{year:04d}"
    if "yyyy-mm" in folded:
        return f"{year:04d}-{month:02d}"
    if "month" in folded and "year" in folded:
        return f"{_MONTHS[month]} {year}"
    return f"{month:02d}/{year:04d}"


def date_variants(value: str) -> list[str]:
    parsed = parse_date(value)
    if parsed is None:
        return [value]
    year, month, day = parsed
    month_name = _MONTHS[month]
    variants = [
        f"{month:02d}/{year:04d}",
        f"{year:04d}-{month:02d}",
        f"{month_name} {year}",
        f"{month:02d}/{day:02d}/{year:04d}",
        f"{year:04d}-{month:02d}-{day:02d}",
        value,
    ]
    seen: list[str] = []
    for item in variants:
        if item not in seen:
            seen.append(item)
    return seen


def _bucket(value: str) -> str:
    text = value.strip().casefold().rstrip(".").replace("’", "'")
    if _DECLINE.search(text):
        return "decline"
    if text in _YES or text.startswith("yes"):
        return "yes"
    if text in _NO or text.startswith("no"):
        return "no"
    if text in _STATES:
        return _STATES[text].casefold()
    if text in _COUNTRIES:
        return _COUNTRIES[text].casefold()
    if text in _DEGREES:
        return _DEGREES[text]
    parsed = parse_date(value)
    if parsed:
        return f"{parsed[0]:04d}-{parsed[1]:02d}"
    return re.sub(r"[^a-z0-9]+", "", text)


def _in_range(value: str, option: str) -> bool:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*", option)
    if not match:
        return False
    try:
        number = float(value)
    except ValueError:
        return False
    return float(match.group(1)) <= number <= float(match.group(2))


def _strip_dial_code(option: str) -> str:
    """"United States +1" is the country, with the phone code on the end."""
    return re.sub(r"\s*\(?\+\d{1,4}\)?\s*$", "", option)


def resolve_option(value: str, options: list[str]) -> str | None:
    if not options:
        return None
    # An answer that is literally one of the options wins before any yes/no bucketing ("Yes- Milpitas, CA only").
    wanted = " ".join(value.split()).casefold()
    for option in options:
        if wanted and " ".join(option.split()).casefold() == wanted:
            return option
    for option in options:
        if _in_range(value, option):
            return option
    target = _bucket(value)
    compact = re.sub(r"[^a-z0-9]+", "", target)
    for option in options:
        if option.strip().casefold() in _EMPTY:
            continue
        if _bucket(_strip_dial_code(option)) == target:
            return option
        if _bucket(option) == target:
            return option
        if target and target in _bucket(option):
            return option
    # "United States" stored, option is "United States of America" (_bucket drops the spaces). Shortest wins
    # over "United States Minor Outlying Islands".
    if len(compact) >= 4:
        hits = [option for option in options if compact in _bucket(_strip_dial_code(option))]
        if hits:
            return min(hits, key=len)
    # "Texas" stored, option is "Texas (TX)". Whole words only: "US" is not in "Australia".
    needle = value.strip().casefold()
    for option in options:
        if needle and re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", option.casefold()):
            return option
    return None


def _words(text: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", text.casefold())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def closest_option(value: str, options: list[str]) -> str | None:
    """Same words, different punctuation: "University of California, Los Angeles (UCLA)" vs "University of California - Los Angeles"."""
    target = _words(value)
    if not target:
        return None
    best_score, best = 0.0, None
    for option in options:
        words = _words(option)
        if not words or words in _EMPTY:
            continue
        score = 1.0 if words == target else SequenceMatcher(None, target, words).ratio()
        if score > best_score:
            best_score, best = score, option
    return best if best_score >= 0.92 else None


def date_part(label: str, raw: str, *, named: bool) -> str | None:
    """"2027-06" for a field labelled "End date year" is 2027; for "End date month" it is June."""
    parsed = parse_date(raw)
    if parsed is None:
        return None
    year, month, _day = parsed
    has_year = bool(re.search(r"\byear\b", label, re.I))
    has_month = bool(re.search(r"\bmonth\b", label, re.I))
    if has_year == has_month:
        # "Graduation month and year" is one field that takes the whole date.
        return None
    if has_year:
        return str(year)
    return _MONTHS[month] if named else f"{month:02d}"


def equivalent(got: str, expected: str) -> bool:
    if got.strip().casefold() == expected.strip().casefold():
        return True
    if _bucket(got) == _bucket(expected) and _bucket(got) != "":
        return True
    got_digits = re.sub(r"\D", "", got)
    expected_digits = re.sub(r"\D", "", expected)
    if len(got_digits) >= 7 and got_digits == expected_digits:
        return True
    return False


def present_value(value: str, *, kind: str, options: list[str], placeholder: str = "", input_type: str = "text") -> str:
    if kind == "file":
        return "uploaded"
    if kind in {"select", "combobox", "radio"}:
        return resolve_option(value, options) or ""
    if kind == "checkbox":
        return "Yes" if _bucket(value) == "yes" else "No"
    if kind == "date":
        return format_date(value, hint=placeholder, input_type=input_type)
    return value


def is_blank(value: str) -> bool:
    return value.strip().casefold() in _EMPTY
