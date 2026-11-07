"""Fill one application page from memory and stop before submit."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pocket_agent.forms.guard import classify_button, progress_is_last
from pocket_agent.forms.match import indexed_key, match_label, section_of, suggestions
from pocket_agent.forms.memory import Memory
from pocket_agent.forms.policy import ask_key, is_sensitive_key, question_for
from pocket_agent.forms.reader import ExtractedField, PageSnapshot, extract_page
from pocket_agent.forms.values import closest_option, date_part, date_variants, is_blank, present_value, resolve_option

_INIT = """
window.__pocketSubmits = 0;
document.addEventListener("submit", event => { if (event.target.dataset.pocketAccount !== 'true') window.__pocketSubmits += 1; }, true);
"""


_RESUME_INIT = """
() => {
  if (window.__pocketSubmits !== undefined) return;
  window.__pocketSubmits = 0;
  document.addEventListener("submit", event => { if (event.target.dataset.pocketAccount !== 'true') window.__pocketSubmits += 1; }, true);
}
"""


@dataclass
class UserAnswer:
    value: str
    always_use: bool = False


@dataclass
class Asked:
    key: str
    label: str
    question: str


AskFn = Callable[[Asked], UserAnswer]


@dataclass
class FieldOutcome:
    label: str
    section: str
    group: int
    key: str | None
    action: str
    value: str
    asked: bool
    from_memory: bool
    via_model: bool = False


@dataclass
class FormRun:
    url: str
    outcomes: list[FieldOutcome] = field(default_factory=list)
    submits: int = 0
    error: str | None = None
    gate: str = ""
    model_calls: int = 0
    model_seconds: float = 0.0


def fill_form(page, url: str, memory: Memory, resume_path: Path | None, ask: AskFn, llm=None, *, resume: bool = False) -> FormRun:
    """resume=True fills the page already open (after the user signed in by hand) instead of loading url."""
    run = FormRun(url=url)
    try:
        page.set_default_timeout(8000)
        if not resume:
            page.add_init_script(_INIT)
            page.goto(url, wait_until="domcontentloaded")
        else:
            # The init script already counts submits on pages loaded since the first run; cover a page that predates it.
            page.evaluate(_RESUME_INIT)
        handled: set[tuple[str, str, int]] = set()
        for step_index in range(48):
            snap = _wait_ready(page) if step_index == 0 else extract_page(page)
            if snap.gate:
                run.gate = snap.gate
                break
            _dismiss(page, snap)
            snap = extract_page(page)
            if snap.gate:
                run.gate = snap.gate
                break
            if _grow_work(page, memory, snap):
                continue
            # _grow_work re-reads the page, which re-stamps every uid. If the page re-rendered meanwhile
            # (Eightfold adds an address block after Country), the old snapshot's uids point at other controls.
            snap = extract_page(page)
            field = _next_field(snap, handled)
            if field is not None:
                try:
                    outcome = _decide_and_write(page, field, memory, resume_path, ask, llm, run, snapshot=snap)
                except Exception as exc:  # noqa: BLE001 — one stuck widget should not end the whole page
                    outcome = _outcome(field, None, "error", type(exc).__name__, asked=False, from_memory=False)
                handled.add((field.section, field.label.casefold(), field.group))
                run.outcomes.append(outcome)
                continue
            if not _advance(page, snap):
                break
        run.submits = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
        if "/submitted" in page.url:
            run.submits = max(run.submits, 1)
    except Exception as exc:  # noqa: BLE001 — the eval harness records crashes per form
        run.error = f"{type(exc).__name__}: {exc}"
    return run


def _wait_ready(page, settle_ms: int = 12000) -> PageSnapshot:
    try:
        page.wait_for_function("() => !document.body.classList.contains('booting')", timeout=8000)
    except Exception:
        pass
    # Client-rendered boards (Workday) show an empty shell at domcontentloaded. Wait for a question, a gate,
    # or a button the loop can use (a step may legitimately start with only "Add another" / "Next").
    snap = extract_page(page)
    waited = 0
    while not _ready(snap) and waited < settle_ms:
        page.wait_for_timeout(250)
        waited += 250
        snap = extract_page(page)
    # Eightfold renders the form in chunks: the first fields appear a second or two before the rest.
    steady = 0
    while snap.fields and not snap.gate and steady < 3 and waited < settle_ms:
        page.wait_for_timeout(250)
        waited += 250
        count = len(snap.fields)
        snap = extract_page(page)
        steady = steady + 1 if len(snap.fields) == count else 0
    return snap


def _ready(snap: PageSnapshot) -> bool:
    if snap.fields or snap.gate:
        return True
    return any(classify_button(button.text, button_type=button.button_type, onclick=button.onclick) in {"forward", "add"} for button in snap.buttons)


def _dismiss(page, snap: PageSnapshot) -> None:
    for _ in range(3):
        targets = [
            button for button in snap.buttons
            if button.in_dialog and classify_button(button.text, button_type=button.button_type, onclick=button.onclick, in_dialog=True) == "dismiss"
        ]
        if not targets:
            return
        page.locator(f'[data-pocket-uid="{targets[0].uid}"]').click()
        page.wait_for_timeout(100)
        snap = extract_page(page)


def _grow_work(page, memory: Memory, snap: PageSnapshot) -> bool:
    needed = len(memory.indexed_values("work"))
    if needed <= 0:
        return False
    grew = False
    for _ in range(6):
        snap = extract_page(page)
        have = _work_groups(snap)
        if have >= needed:
            return grew
        button = _add_button(snap, have)
        if button is None:
            return grew
        before = have
        page.locator(f'[data-pocket-uid="{button.uid}"]').click()
        page.wait_for_timeout(50)
        snap = extract_page(page)
        if _work_groups(snap) <= before:
            return grew
        grew = True
    return grew


def _work_groups(snap: PageSnapshot) -> int:
    groups = {field.group for field in snap.fields if field.section == "work" and field.label}
    if not groups:
        return 0
    return max(groups) + 1


def _add_button(snap: PageSnapshot, have: int = 0):
    for button in snap.buttons:
        kind = classify_button(button.text, button_type=button.button_type, onclick=button.onclick)
        section = section_of(button.heading)
        # An unlabeled "Add another" only counts as work history once the page already shows a work block.
        if kind == "add" and (section == "work" or (section == "" and have > 0)):
            return button
    return None


def _next_field(snap: PageSnapshot, handled: set[tuple[str, str, int]]) -> ExtractedField | None:
    pending = []
    for field in snap.fields:
        if (field.section, field.label.casefold(), field.group) in handled:
            continue
        pending.append(field)
    files = [field for field in pending if field.kind == "file"]
    return files[0] if files else (pending[0] if pending else None)


def _name_confirmation(page, field: ExtractedField, memory: Memory, snapshot: PageSnapshot | None) -> str | None:
    """Confirm only an entered name matching the profile, not a general attestation."""
    label = field.label.casefold()
    if not re.match(r"^have you (?:added|entered|provided) your full legal name\b", label):
        return None
    if ask_key(field.label, field.section):
        return None
    expected = memory.get("full_name")
    if not expected or not expected.value.strip() or snapshot is None:
        return None
    normalize = lambda value: " ".join(value.split()).casefold()
    names: dict[str, list[str]] = {}
    for candidate in snapshot.fields:
        if candidate.uid == field.uid or candidate.kind != "text":
            continue
        matched = match_label(candidate.label, section=candidate.section)
        if matched and matched.key in {"full_name", "first_name", "last_name"}:
            names.setdefault(matched.key, []).append(_read_back(page, candidate))
    target = normalize(expected.value)
    if len(names.get("full_name", [])) == 1 and normalize(names["full_name"][0]) == target:
        return "Yes"
    if len(names.get("first_name", [])) == len(names.get("last_name", [])) == 1:
        if normalize(names["first_name"][0] + " " + names["last_name"][0]) == target:
            return "Yes"
    return None


def _decide_and_write(page, field: ExtractedField, memory: Memory, resume_path, ask: AskFn, llm, run: FormRun, *, snapshot: PageSnapshot | None = None) -> FieldOutcome:
    forced = ask_key(field.label, field.section)
    if field.kind == "file" and forced == "cover_letter":
        # The only file we have is the resume. It does not go in a cover letter slot.
        return _outcome(field, forced, "skip", "", asked=False, from_memory=False)
    if field.kind == "file" and resume_path is not None and not resume_names_user(Path(resume_path), memory):
        # Boards parse the upload and overwrite name, email, and phone with what it says. Never send someone else's resume.
        return _outcome(field, "resume", "skip", "", asked=False, from_memory=False)
    if forced:
        return _ask_or_saved(page, field, memory, resume_path, ask, forced)
    confirmed = _name_confirmation(page, field, memory, snapshot)
    if confirmed:
        written = _write(page, field, confirmed, resume_path)
        if written:
            return _outcome(field, "full_name_confirmation", "fill", written, asked=False, from_memory=True)
    if field.kind == "checkbox" and not field.required and ask_key(field.label, field.section):
        return _outcome(field, None, "skip", "", asked=False, from_memory=False)
    learned = memory.learned_labels()
    match = match_label(field.label, section=field.section, learned=learned)
    base = match.key if match and match.score >= 0.93 and not is_sensitive_key(match.key) else None
    key = indexed_key(base, field.group) if base else None
    value = stored_value(memory, key, field.label) if key else ""
    ruled = bool(key and value.strip() and match and match.reason in {"exact", "tokens", "subset", "learned"})
    agreed = False
    if ruled and llm is not None and getattr(llm, "available", False) and match is not None and match.reason != "exact":
        import time
        started = time.perf_counter()
        agreed = _llm_fill(field, memory, llm) == key
        run.model_calls += 1
        run.model_seconds += time.perf_counter() - started
    if ruled:
        written = _write(page, field, value, resume_path)
        if written:
            return _outcome(field, key, "fill", written, asked=False, from_memory=True, via_model=agreed)
        if field.required:
            return _ask_or_saved(page, field, memory, resume_path, ask, key or _label_key(field))
    if field.required:
        return _ask_or_saved(page, field, memory, resume_path, ask, key or _label_key(field))
    if field.value and not is_blank(field.value):
        _clear_field(page, field)
    return _outcome(field, key, "skip", "", asked=False, from_memory=False)


def _label_key(field: ExtractedField) -> str:
    """A question no rule knows is saved under its own wording, so one answer never fills another question."""
    words = re.sub(r"[^a-z0-9]+", " ", field.label.casefold()).split()
    return "q." + "_".join(words[:12])


def _ask_or_saved(page, field, memory, resume_path, ask, key: str) -> FieldOutcome:
    entry = memory.get(key)
    sensitive = is_sensitive_key(key) or ask_key(field.label, field.section) == key
    if entry and entry.always_use and entry.value.strip():
        written = _write(page, field, entry.value, resume_path)
        if written:
            return _outcome(field, key, "fill", written, asked=False, from_memory=True)
    question = question_for(field.label)
    if field.kind == "dropdown":
        offered = _peek_dropdown(page, field)
        if 0 < len(offered) <= 15:
            question += " Options: " + " / ".join(offered)
    answer = ask(Asked(key, field.label, question))
    memory.put(
        key,
        answer.value,
        always_use=answer.always_use,
        sensitive=sensitive,
        source="answer",
        label=field.label,
    )
    written = _write(page, field, answer.value, resume_path) if answer.value.strip() else ""
    if not written and field.value and not is_blank(field.value):
        _clear_field(page, field)
    return _outcome(field, key, "ask", written, asked=True, from_memory=False)


def resume_names_user(path: Path, memory: Memory) -> bool:
    """True when the resume file mentions the profile's name (or there is no name to check against)."""
    entry = memory.get("full_name")
    name = entry.value.strip().casefold() if entry and entry.value else ""
    if not name:
        return True
    try:
        if path.suffix.casefold() == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((pdf_page.extract_text() or "") for pdf_page in PdfReader(str(path)).pages)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — an unreadable file is not proof it is the wrong person
        return True
    flat = " ".join(text.split()).casefold()
    return name in flat or all(part in flat for part in name.split())


def stored_value(memory: Memory, key: str | None, label: str) -> str:
    if not key:
        return ""
    if key == "previously_employed_here":
        low = label.casefold()
        for mem_key in memory.keys():
            if not mem_key.endswith(".org"):
                continue
            entry = memory.get(mem_key)
            if entry and entry.value and entry.value.casefold() in low:
                return "Yes"
        return "No"
    entry = memory.get(key)
    return entry.value if entry and entry.value.strip() else ""


def _llm_fill(field: ExtractedField, memory: Memory, llm) -> str | None:
    ranked = suggestions(field.label, field.section, limit=8)
    candidates = []
    seen: set[str] = set()
    for base, score in ranked:
        if score < 0.2 or base in seen or is_sensitive_key(base):
            continue
        seen.add(base)
        key = indexed_key(base, field.group)
        value = stored_value(memory, key, field.label)
        if value.strip():
            candidates.append((key, value))
    if not candidates:
        return None
    choice = llm.choose(
        label=field.label,
        kind=field.kind,
        options=[option["label"] for option in field.options],
        candidates=candidates[:6],
    )
    if choice is None or choice.action != "fill":
        return None
    if choice.key not in {key for key, _value in candidates}:
        return None
    return choice.key


def _write(page, field: ExtractedField, raw: str, resume_path: Path | None) -> str:
    options = [option["label"] for option in field.options]
    if field.kind == "file":
        if resume_path is None:
            return ""
        page.locator(f'[data-pocket-uid="{field.uid}"]').set_input_files(str(resume_path))
        _settle_upload(page, field.uid)
        return "uploaded"
    part = date_part(field.label, raw, named=field.kind in {"dropdown", "select", "combobox"})
    if part:
        raw = part
    if field.kind == "dropdown":
        return _pick_dropdown(page, field, raw)
    target = present_value(raw, kind=field.kind, options=options, placeholder=field.placeholder, input_type=field.input_type)
    if field.kind == "date":
        variants = [target] + [item for item in date_variants(raw) if item != target]
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", raw.strip()):
            # A full day was given ("today's date"); do not shorten it to month/year first.
            variants = [raw.strip()] + [item for item in variants if item != raw.strip()]
    else:
        variants = [target]
    national = field.kind == "text" and bool(re.match(r"^\+\d{1,3}[\s-]+\(?\d", target or ""))
    if national:
        # Boards with a separate country-code picker (Eightfold) reject "+1-626-..." and want the national number.
        variants.append(re.sub(r"^\+\d{1,3}[\s-]+", "", target))
    if not target and field.kind in {"select", "combobox", "radio"}:
        return ""
    if field.input_type == "number":
        target = target.replace(",", "").strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?", target):
            # A number box refuses text; leave it for the user instead of crashing the run.
            return ""
        variants = [target]
    written = ""
    for variant in variants:
        if not variant:
            continue
        _apply(page, field, variant)
        if national:
            # Those boards validate on blur. blur() rather than a key press, so nothing can submit.
            page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate("el => el.blur()")
            page.wait_for_timeout(300)
        page.wait_for_timeout(30)
        if not _invalid(page, field.uid):
            written = _read_back(page, field)
            if written and set(written) <= set("•*"):
                written = variant
            break
    return written


_UPLOAD_BUSY = r"""
(uid) => {
  const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
  if (!el) return false;
  const block = el.closest("li, [class*='question'], [class*='field'], [role=group]") || el.parentElement;
  return [...block.querySelectorAll("*")].some((node) => {
    if (!node.offsetParent || node.children.length) return false;
    return /analy[sz]ing|parsing|uploading|processing/i.test(node.textContent || "");
  });
}
"""


def _settle_upload(page, uid: str) -> None:
    """Wait while the board reads the resume. Its autofill lands then, before our values, not on top of them."""
    page.wait_for_timeout(300)
    for _ in range(80):
        if not page.evaluate(_UPLOAD_BUSY, uid):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(300)


_NEARBY_OPTIONS = r"""
(uid) => {
  document.querySelectorAll("[data-pocket-pick]").forEach((node) => node.removeAttribute("data-pocket-pick"));
  const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
  if (!el || !el.parentElement) return [];
  const seen = [];
  for (const node of el.parentElement.querySelectorAll("[role=option], [class*='result'] > *, [class*='suggestion'], [class*='option']")) {
    if (node.contains(el) || !node.offsetParent || node.querySelector("[role=option]")) continue;
    const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
    if (!text || /no .*found|loading|no results/i.test(text)) continue;
    node.setAttribute("data-pocket-pick", String(seen.length));
    seen.push(text);
  }
  return seen;
}
"""


def _pick_nearby(page, field: ExtractedField, value: str) -> bool:
    """Type like a person so the board searches, then click the suggestion that matches the answer."""
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    for query in dict.fromkeys([value, value.split(",")[0].strip()]):
        if not query:
            continue
        root.click()
        root.fill("")
        root.press_sequentially(query, delay=25)
        for _ in range(12):
            page.wait_for_timeout(250)
            labels = page.evaluate(_NEARBY_OPTIONS, field.uid) or []
            choice = resolve_option(value, labels) or closest_option(value, labels)
            if choice:
                page.locator(f'[data-pocket-pick="{labels.index(choice)}"]').click()
                page.wait_for_timeout(100)
                return True
    root.fill(value)
    return False


_BOX = r"""
(uid) => {
  document.querySelectorAll("[data-pocket-box]").forEach((el) => el.removeAttribute("data-pocket-box"));
  const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
  if (!el) return;
  const box = el.closest(".select-shell") || el.closest("[class*='select__container']") || el.parentElement;
  box.setAttribute("data-pocket-box", uid);
}
"""

_BOX_OPTIONS = r"""
(uid) => {
  // Some selects render options into a separate listbox, named by aria-controls, once opened.
  const input = document.querySelector(`[data-pocket-uid="${uid}"]`);
  const list = input && document.getElementById(input.getAttribute("aria-controls") || "");
  if (list && !list.hasAttribute("data-pocket-box")) {
    document.querySelectorAll(`[data-pocket-box="${uid}"]`).forEach((el) => el.removeAttribute("data-pocket-box"));
    list.setAttribute("data-pocket-box", uid);
  }
  const box = document.querySelector(`[data-pocket-box="${uid}"]`);
  if (!box) return null;
  const labels = [...box.querySelectorAll("[role=option]")].map((el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim());
  const text = (box.innerText || "").toLowerCase();
  const loading = /loading/.test(text);
  return { labels, loading, done: !loading && (labels.length > 0 || /no options|no results|not found/.test(text)) };
}
"""


def _dropdown_queries(raw: str) -> list[str]:
    """Empty first (short lists show everything), then the answer, then shorter searches for long names.

    Search boxes match the typed text literally, so "University of California, Los Angeles" finds nothing
    when the site spells it "University of California - Los Angeles". The shorter searches drop punctuation.
    """
    plain = re.sub(r"\s*\([^)]*\)", "", raw).strip()
    words = re.sub(r"[^\w']+", " ", plain).split()
    queries = [""]
    for query in (raw, plain, " ".join(words)):
        if query and query not in queries:
            queries.append(query)
    for count in range(len(words) - 1, 1, -1):
        prefix = " ".join(words[:count])
        if prefix not in queries:
            queries.append(prefix)
    return queries[:6]


def _dropdown_labels(page, uid: str, before: dict | None = None) -> list[str]:
    """Options for the text just typed.

    An async list keeps showing the last search ("No options", or the old matches) until its debounce
    fires, so a reading only counts once the list has visibly changed or had time to, and it must read
    the same twice in a row.
    """
    old = before["labels"] if before else None
    moved = before is None
    last = None
    for step in range(20):
        page.wait_for_timeout(150)
        seen = page.evaluate(_BOX_OPTIONS, uid)
        if not seen:
            continue
        if seen["loading"] or seen["labels"] != old:
            moved = True
        if not moved and step < 6:
            continue
        if not seen["done"]:
            last = None
            continue
        if seen["labels"] == last:
            return list(last)
        last = seen["labels"]
    return list(last or [])


def _peek_dropdown(page, field: ExtractedField) -> list[str]:
    """Open the menu with no search, note what it offers, and close it again."""
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    page.evaluate(_BOX, field.uid)
    root.click()
    root.fill("")
    labels = _dropdown_labels(page, field.uid)
    root.press("Escape")
    return labels


def _pick_dropdown(page, field: ExtractedField, raw: str) -> str:
    """Open a search-select, look at what it offers, and click an option only when one clearly matches."""
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    page.evaluate(_BOX, field.uid)
    options = page.locator(f'[data-pocket-box="{field.uid}"] [role=option]')
    for query in _dropdown_queries(raw):
        # Clicking a react-select input while its menu is open closes it, and a closed menu does not search.
        if root.get_attribute("aria-expanded") != "true":
            root.click()
        before = page.evaluate(_BOX_OPTIONS, field.uid)
        root.fill(query)
        labels = _dropdown_labels(page, field.uid, before)
        choice = resolve_option(raw, labels) or closest_option(raw, labels)
        if choice:
            options.nth(labels.index(choice)).click()
            page.wait_for_timeout(50)
            if root.count() == 0:
                # Picking it removed the field (Greenhouse drops race and resets Hispanic/Latino on "Decline").
                return choice
            return _read_back(page, field) or choice
    root.fill("")
    root.press("Escape")
    return ""


def _apply(page, field: ExtractedField, value: str) -> None:
    root = page.locator(f'[data-pocket-uid="{field.uid}"]')
    if field.kind == "checkbox":
        root.set_checked(value.casefold() in {"yes", "true"})
        return
    if field.kind == "radio":
        match = next((option for option in field.options if option["label"].casefold() == value.casefold()), None)
        if match and match["uid"]:
            page.locator(f'[data-pocket-uid="{match["uid"]}"]').check()
        return
    if field.kind == "select":
        root.select_option(label=value)
        return
    if field.kind == "combobox":
        root.click()
        match = next((option for option in field.options if option["label"].casefold() == value.casefold()), None)
        if match and match["uid"]:
            page.locator(f'[data-pocket-uid="{match["uid"]}"]').click()
        return
    if field.kind == "typeahead":
        root.click()
        root.fill(value[:12])
        page.wait_for_timeout(30)
        if field.list_id:
            page.evaluate(
                """(id) => {
                  const list = document.getElementById(id);
                  const input = document.querySelector('[aria-controls="' + id + '"]');
                  if (!list || !input) return;
                  const query = (input.value || "").toLowerCase();
                  list.hidden = false;
                  list.querySelectorAll("[role=option]").forEach((item) => {
                    item.hidden = query.length >= 2 && !(item.textContent || "").toLowerCase().includes(query);
                  });
                }""",
                field.list_id,
            )
            choice = page.locator(f"#{field.list_id} [role=option]").filter(has_text=value)
            if choice.count():
                choice.first.click()
                return
            root.fill(value)
            return
        _pick_nearby(page, field, value)
        return
    picker = field.kind == "date" and root.get_attribute("aria-haspopup") == "dialog"
    if picker and root.evaluate("el => el.readOnly"):
        root.click()  # Eightfold's date picker only accepts typing once it is open.
    root.fill(value)
    if picker:
        root.press("Tab")  # Commit and close the calendar. Never Enter: that can submit the form.


def _clear_field(page, field: ExtractedField) -> None:
    if field.kind in {"file", "radio", "combobox", "typeahead", "dropdown"}:
        return
    locator = page.locator(f'[data-pocket-uid="{field.uid}"]')
    if field.kind == "checkbox":
        locator.set_checked(False)
        return
    locator.fill("")


def _read_back(page, field: ExtractedField) -> str:
    if field.kind == "radio":
        for option in field.options:
            if option["uid"] and page.locator(f'[data-pocket-uid="{option["uid"]}"]').is_checked():
                return option["label"]
        return ""
    if field.kind == "checkbox":
        return "Yes" if page.locator(f'[data-pocket-uid="{field.uid}"]').is_checked() else "No"
    if field.kind == "dropdown":
        return page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate(
            """(el) => {
              const shell = el.closest(".select-shell") || el.closest("[class*='select__container']") || el.parentElement;
              const value = shell && shell.querySelector("[class*='single-value']");
              return value ? (value.innerText || value.textContent || "").trim() : (el.value || "").trim();
            }"""
        )
    if field.kind == "combobox":
        raw = page.locator(f'[data-pocket-uid="{field.uid}"]').inner_text().strip()
        return "" if raw.casefold() in {"select one", "select", "choose", "choose one"} else raw
    if field.kind == "select":
        return page.locator(f'[data-pocket-uid="{field.uid}"] option:checked').inner_text().strip()
    if field.kind == "file":
        count = page.locator(f'[data-pocket-uid="{field.uid}"]').evaluate("el => el.files ? el.files.length : 0")
        return "uploaded" if count else ""
    return page.locator(f'[data-pocket-uid="{field.uid}"]').input_value()


def _invalid(page, uid: str) -> bool:
    return bool(page.evaluate(
        """(uid) => {
          const el = document.querySelector(`[data-pocket-uid="${uid}"]`);
          if (!el) return false;
          if (el.getAttribute("aria-invalid") === "true") return true;
          const box = el.parentElement;
          return !!(box && box.querySelector("[role=alert]"));
        }""",
        uid,
    ))


def _advance(page, snap: PageSnapshot) -> bool:
    if progress_is_last(snap.progress):
        return False
    forwards = []
    for button in snap.buttons:
        kind = classify_button(
            button.text,
            button_type=button.button_type,
            onclick=button.onclick,
            in_dialog=button.in_dialog,
            heading=button.heading,
        )
        if kind == "forward" and not button.in_dialog:
            forwards.append(button)
    if not forwards:
        return False
    before = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
    page.locator(f'[data-pocket-uid="{forwards[0].uid}"]').click()
    page.wait_for_timeout(80)
    after = int(page.evaluate("() => window.__pocketSubmits || 0") or 0)
    return after == before and "/submitted" not in page.url


def _outcome(field: ExtractedField, key: str | None, action: str, value: str, *, asked: bool, from_memory: bool, via_model: bool = False) -> FieldOutcome:
    return FieldOutcome(
        label=field.label,
        section=field.section,
        group=field.group,
        key=key,
        action=action,
        value=value,
        asked=asked,
        from_memory=from_memory,
        via_model=via_model,
    )
