"""Resume text extraction. PDF via pypdf, plain text directly."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

from pocket_agent.paths import examples_dir


def resume_contact_profile(profile: dict, path: Path) -> dict:
    """Prefer unambiguous resume contacts; do not infer citizenship or other absent facts."""
    parsed = parse_resume(path)
    if not parsed.get("ok"):
        raise ValueError(parsed.get("error", "Could not read resume"))
    result = deepcopy(profile)
    personal = profile.get("personal") or {}
    for key in ("name", "email", "phone"):
        if personal.get(key):
            result[key] = personal[key]
    for key in ("city", "region", "country", "postal_code", "county", "address_line_1", "address_line_2"):
        if personal.get(key):
            result.setdefault("location", {})[key] = personal[key]
    emails = list(dict.fromkeys(parsed["emails"]))
    if len(emails) == 1:
        result["email"] = emails[0]
    elif len(emails) > 1:
        raise ValueError("Resume has multiple email addresses; use a resume with one application contact email.")
    # Missing facts retain the explicit personal/profile fallback.
    lines = parsed["lines"]
    if lines:
        name = lines[0].strip()
        # Accept only a standalone name line, not a heading, email, or contact row.
        if re.fullmatch(r"[^\W\d_]+(?:[ .’'\-]+[^\W\d_]+){1,5}", name, re.UNICODE) and name.casefold() not in {"curriculum vitae", "professional summary", "software engineer", "contact information"}:
            result["name"] = name
    header = "\n".join(lines[:5])
    phones = re.findall(r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?!\d)", header)
    if len(set(phones)) == 1:
        result["phone"] = phones[0]
    for key, domain in (("github", "github.com"), ("linkedin", "linkedin.com/in")):
        urls = re.findall(r"(?:https?://)?(?:www\.)?" + re.escape(domain) + r"/[\w./%-]+", parsed["text"], re.I)
        if len(set(urls)) == 1:
            result.setdefault("links", {})[key] = urls[0] if urls[0].startswith("http") else "https://" + urls[0]
    return result


def parse_resume(path: Path | None = None, *, home: Path | None = None) -> dict:
    target = _resolve(path, home)
    if target is None:
        return {"ok": False, "error": "no resume file found"}
    suffix = target.suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(target)
    elif suffix in {".txt", ".md"}:
        text = target.read_text(encoding="utf-8")
    else:
        return {"ok": False, "error": f"unsupported resume type {suffix}"}
    return {
        "ok": True,
        "path": str(target),
        "text": text.strip(),
        "emails": re.findall(r"[\w.+-]+@[\w.-]+", text),
        "lines": [line.strip() for line in text.splitlines() if line.strip()][:40],
    }


def _resolve(path: Path | None, home: Path | None) -> Path | None:
    if path is not None:
        return path if path.exists() else None
    if home is not None:
        for name in ("resume.pdf", "resume.txt"):
            candidate = home / name
            if candidate.exists():
                return candidate
    for name in ("resume.txt", "resume.pdf"):
        candidate = examples_dir() / name
        if candidate.exists():
            return candidate
    return None


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)
