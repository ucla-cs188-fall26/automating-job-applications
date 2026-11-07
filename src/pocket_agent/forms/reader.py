"""Read form controls from a live page. No model calls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pocket_agent.forms.match import section_of

_EXTRACT = r"""
() => {
  document.querySelectorAll("[data-pocket-uid]").forEach((el) => el.removeAttribute("data-pocket-uid"));
  const textOf = (el) => ((el && (el.innerText || el.textContent)) || "").replace(/\s+/g, " ").trim();
  const clean = (value) => (value || "").replace(/[*\u2731]/g, "").replace(/\s+/g, " ").trim();
  // Labels whose raw text carried a required mark (* or Lever's \u2731) before clean() dropped it.
  const marked = new Set();
  const note = (el, node) => {
    if (/[*\u2731]/.test((node && node.textContent) || "")) marked.add(el);
  };
  const visible = (el) => {
    if (!el || el.closest("template")) return false;
    if (el.closest("[hidden]")) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const heading = (el) => {
    let best = "";
    for (const node of document.querySelectorAll("h1,h2,h3,h4")) {
      if (!visible(node)) continue;
      if (el.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING) best = textOf(node);
    }
    return best;
  };
  let seq = 0;
  const stamp = (el) => {
    const id = "pk" + (++seq);
    el.setAttribute("data-pocket-uid", id);
    return id;
  };
  // The label element of the question block around a control (Lever: .application-label next to .application-field).
  // Climbs only while the block holds no other question's control, so a neighbour's label is never borrowed.
  const questionLabel = (el) => {
    let node = el.parentElement;
    for (let depth = 0; node && depth < 6 && node !== document.body; depth += 1, node = node.parentElement) {
      const others = [...node.querySelectorAll("input:not([type=hidden]), select, textarea")].filter((other) => other !== el && !(el.name && other.name === el.name) && visible(other));
      if (others.length) return "";
      const found = [...node.querySelectorAll("[class*='label']")].filter((cand) => !cand.contains(el) && !cand.closest("a, button") && !cand.querySelector("input,select,textarea") && (cand.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) && clean(textOf(cand)));
      if (found.length) {
        const best = found[found.length - 1];
        note(el, best);
        return clean(textOf(best));
      }
    }
    return "";
  };
  const labelFor = (el) => {
    if (el.type === "file") {
      const group = el.closest("[role=group][aria-labelledby]");
      const named = group && group !== el ? labelFor(group) : "";
      if (named) return named;
    }
    const aria = el.getAttribute && el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const labelled = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelled) {
      const text = labelled.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(textOf).join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && clean(textOf(lab))) return clean(textOf(lab));
    }
    const wrap = el.closest && el.closest("label");
    if (wrap) {
      // A wrapping <label> often also holds status text, menus, or help. Its own label element is the question.
      const own = [...wrap.querySelectorAll("[class*='label']")].find((node) => !node.contains(el) && !node.querySelector("input,select,textarea") && clean(textOf(node)));
      if (own) {
        note(el, own);
        return clean(textOf(own));
      }
      const clone = wrap.cloneNode(true);
      clone.querySelectorAll("input,select,textarea,button").forEach((node) => node.remove());
      if (clean(textOf(clone))) return clean(textOf(clone));
    }
    const asked = questionLabel(el);
    if (asked) return asked;
    const prev = el.previousElementSibling;
    if (prev && clean(textOf(prev)) && clean(textOf(prev)).length < 180) return clean(textOf(prev));
    const placeholder = el.getAttribute && el.getAttribute("placeholder");
    if (placeholder && !/mm|yyyy/i.test(placeholder)) return clean(placeholder);
    return "";
  };
  const fields = [];
  const consumed = new Set();
  const radios = [...document.querySelectorAll("input[type=radio]")].filter(visible);
  const groups = new Map();
  for (const radio of radios) {
    const name = radio.name || stamp(radio);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(radio);
    consumed.add(radio);
  }
  for (const group of groups.values()) {
    const fieldset = group[0].closest("fieldset");
    const legend = fieldset && fieldset.querySelector("legend");
    const label = legend ? clean(textOf(legend)) : (questionLabel(group[0]) || labelFor(group[0]));
    const options = group.map((radio) => ({ label: labelFor(radio) || radio.value, uid: stamp(radio) }));
    const checked = group.find((radio) => radio.checked);
    fields.push({
      uid: options[0].uid,
      order: group[0].getBoundingClientRect().top * 1000 + group[0].getBoundingClientRect().left,
      label,
      kind: "radio",
      required: group.some((radio) => radio.required) || (legend ? /[*\u2731]/.test(legend.textContent) : marked.has(group[0])),
      options,
      placeholder: "",
      heading: heading(group[0]),
      value: checked ? (labelFor(checked) || checked.value) : "",
      input_type: "radio",
    });
  }
  const controls = [...document.querySelectorAll('input, textarea, select, button[role=combobox], button[aria-haspopup=listbox][name]')];
  for (const el of controls) {
    // Upload widgets often hide the real file input behind a visible drop zone.
    const shown = visible(el) || (el.type === "file" && (
      visible(el.parentElement) || visible(el.closest('[data-automation-id="file-upload-drop-zone"]'))));
    if (consumed.has(el) || !shown) continue;
    // react-select keeps a hidden "required" input next to its combobox. It is not a question.
    if (el.getAttribute("role") !== "combobox" && el.type !== "file" && el.closest(".select-shell, [class*='select__container']")) continue;
    const type = (el.getAttribute("type") || el.type || "text").toLowerCase();
    if (type === "password") continue;
    // Honeypots (Workday's "beecatcher": "This input is for robots only") must stay empty.
    if (el.getAttribute("data-automation-id") === "beecatcher" || /for robots only|do not enter if you'?re human/i.test(labelFor(el))) continue;
    if (["hidden", "submit", "button", "reset", "radio", "checkbox"].includes(type) && el.getAttribute("role") !== "combobox" && !(el.tagName === 'BUTTON' && el.name && el.getAttribute('aria-haspopup') === 'listbox')) {
      if (type !== "checkbox") continue;
    }
    const workdayResume = type === "file" && /\.myworkday(?:jobs|site)\.com$/.test(location.hostname)
      && /autofill with resume/i.test(document.body.innerText) && document.querySelectorAll('input[type=file]').length === 1;
    const workday = /\.myworkday(?:jobs|site)\.com$/.test(location.hostname);
    const workdaySelect = workday && el.tagName === 'BUTTON' && el.getAttribute('aria-haspopup') === 'listbox' && el.name;
    let label = workdayResume ? "Resume" : (labelFor(el) || (type === "file" ? heading(el) : ""));
    if (workdaySelect) {
      const lab = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      label = lab ? clean(textOf(lab)) : clean(label.replace(textOf(el), '').replace(/\brequired\b/ig, ''));
    }
    const placeholder = el.getAttribute("placeholder") || "";
    let kind = "text";
    let options = [];
    let value = "";
    const listbox = document.getElementById(el.getAttribute("aria-controls") || "");
    // Some selects (Eightfold) only build their listbox when opened; options are read when filling.
    const lazyList = el.tagName === "INPUT" && el.getAttribute("role") === "combobox" && !el.readOnly && !!el.getAttribute("aria-controls") && !(listbox && listbox.querySelector("[role=option]"));
    if (workdaySelect) {
      kind = 'workday_select';
      value = /^(select one|select|choose one|choose)$/i.test(clean(textOf(el))) ? '' : clean(textOf(el));
    } else if (workday && el.getAttribute('data-uxi-widget-type') === 'selectinput' && el.getAttribute('enterkeyhint') === 'search') {
      kind = 'workday_prompt';
      let parent = el.parentElement;
      for (let level=0; parent && level<4; level++, parent=parent.parentElement) {
        const selected = parent.querySelector('[role=listbox][aria-label*="selected" i]');
        if (selected) { value = clean(textOf(selected)); break; }
      }
    } else if (el.getAttribute("role") === "combobox" && el.tagName === "INPUT" && el.getAttribute("aria-haspopup") === "dialog") {
      // A calendar picker that also accepts a typed date (Eightfold).
      kind = "date";
      value = el.value || "";
    } else if (el.getAttribute("role") === "combobox" && el.tagName === "INPUT" && ((!listbox && (el.getAttribute("aria-autocomplete") || "") === "list") || lazyList)) {
      // Options are not in the page until the menu opens, so they are read when filling.
      kind = "dropdown";
      const shell = el.closest(".select-shell") || el.closest("[class*='select__container']") || el.parentElement;
      const single = shell && shell.querySelector("[class*='single-value']");
      value = single ? clean(textOf(single)) : (lazyList ? el.value || "" : "");
    } else if (el.getAttribute("role") === "combobox") {
      kind = "combobox";
      options = listbox ? [...listbox.querySelectorAll("[role=option]")].map((opt) => ({ label: clean(textOf(opt)), uid: stamp(opt) })) : [];
      const raw = clean(el.dataset.value || textOf(el));
      value = /^(select one|select|choose one|choose)$/i.test(raw) ? "" : raw;
    } else if (el.tagName === "SELECT") {
      kind = "select";
      options = [...el.options].map((opt) => clean(opt.text)).filter((text) => text && !/^select one$/i.test(text)).map((text) => ({ label: text, uid: "" }));
      const selected = el.selectedOptions[0];
      value = selected && selected.value ? clean(selected.text) : "";
    } else if (type === "checkbox") {
      kind = "checkbox";
      value = el.checked ? "Yes" : "No";
    } else if (type === "file") {
      kind = "file";
      value = el.files && el.files.length ? "uploaded" : "";
    } else if ((el.getAttribute("aria-autocomplete") || "") === "list" || (el.tagName === "INPUT" && el.parentElement && el.parentElement.querySelector(":scope > [class*='dropdown'], :scope > [class*='autocomplete']"))) {
      kind = "typeahead";
      value = el.value || "";
    } else if (el.tagName === "TEXTAREA") {
      kind = "textarea";
      value = el.value || "";
    } else {
      kind = "text";
      value = el.value || "";
      if (type === "date" || /date/i.test(label) || /mm|yyyy/i.test(placeholder)) kind = "date";
    }
    const spot = (visible(el) ? el : el.parentElement).getBoundingClientRect();
    fields.push({
      uid: stamp(el),
      order: spot.top * 1000 + spot.left,
      label,
      kind,
      required: el.required || el.getAttribute("aria-required") === "true" || !!el.closest("[role=group][aria-required=true]") || marked.has(el) || (workdaySelect && /\brequired\b/i.test(el.getAttribute('aria-label') || '')),
      options,
      placeholder,
      heading: heading(el),
      value,
      input_type: type,
      list_id: el.getAttribute("aria-controls") || "",
    });
  }
  const buttons = [];
  for (const button of document.querySelectorAll("button")) {
    if (!visible(button) || button.getAttribute("role") === "combobox" || (button.name && button.getAttribute('aria-haspopup') === 'listbox')) continue;
    buttons.push({
      uid: stamp(button),
      text: clean(textOf(button) || button.getAttribute("aria-label") || button.getAttribute("title") || ""),
      type: button.type || "submit",
      onclick: button.getAttribute("onclick") || "",
      heading: heading(button),
      in_dialog: !!button.closest("[role=dialog]"),
    });
  }
  fields.sort((a, b) => a.order - b.order);
  // Workday: <li data-automation-id="progressBarActiveStep">current step 2 of 7 ...</li>
  const progressNode = document.getElementById("progress") || document.querySelector("[data-automation-id=progressBarActiveStep]");
  const progress = progressNode ? textOf(progressNode) : "";
  const alerts = [...document.querySelectorAll("[role=alert]")].filter(visible).map(textOf);
  const blob = (document.body.innerText || "").toLowerCase();
  let gate = "";
  // A challenge drawn in a visible iframe never shows up in body text. A hidden invisible-mode badge is not a challenge.
  const challenge = [...document.querySelectorAll("iframe")].some((frame) => /captcha|challenges\.cloudflare/i.test((frame.src || "") + " " + (frame.title || "")) && visible(frame) && frame.getBoundingClientRect().width > 60 && frame.getBoundingClientRect().height > 60);
  if (challenge || /captcha|i'm not a robot|i am not a robot|verify you are human/.test(blob)) gate = "captcha";
  else if (/verify your (email|account)|verification (code|email)|check your (email|inbox)|one.time (code|password)|two.factor/.test(blob)) gate = "verification";
  else if (document.querySelector('input[type="password"]') || /create an account|create your account|sign in to apply|account already exists|already have an account/.test(blob)) gate = "login";
  // A modal asking the user to agree (Eightfold's "Data Privacy Agreement" after a resume upload) blocks every
  // click behind it. Agreeing is the user's call, and its buttons are type=submit, so hand the browser over.
  else if ([...document.querySelectorAll("[role=dialog][aria-modal=true], [role=alertdialog]")].some((box) => visible(box) && !/cookie/i.test(textOf(box)) && [...box.querySelectorAll("button")].some((el) => visible(el) && /^(i agree|agree|i accept|accept)$/i.test(clean(textOf(el)))))) gate = "consent";
  else if (!fields.length && [...document.querySelectorAll("a, button")].some((el) => visible(el) && (/^(adventureButton|applyManually)$/.test(el.getAttribute("data-automation-id") || "") || /^(apply|apply now|apply manually|start (your )?application)$/i.test(clean(textOf(el)))))) gate = "start";
  return { fields, buttons, progress, alerts, gate };
}
"""


@dataclass
class ExtractedField:
    uid: str
    label: str
    kind: str
    required: bool
    options: list[dict[str, str]]
    placeholder: str
    heading: str
    value: str
    input_type: str
    list_id: str = ""
    section: str = ""
    group: int = 0


@dataclass
class ExtractedButton:
    uid: str
    text: str
    button_type: str
    onclick: str
    heading: str
    in_dialog: bool


@dataclass
class PageSnapshot:
    fields: list[ExtractedField] = field(default_factory=list)
    buttons: list[ExtractedButton] = field(default_factory=list)
    progress: str = ""
    alerts: list[str] = field(default_factory=list)
    gate: str = ""


def extract_page(page) -> PageSnapshot:
    raw = page.evaluate(_EXTRACT)
    fields = [
        ExtractedField(
            uid=item["uid"],
            label=item["label"],
            kind=item["kind"],
            required=bool(item["required"]),
            options=list(item["options"]),
            placeholder=item["placeholder"],
            heading=item["heading"],
            value=item["value"] or "",
            input_type=item["input_type"],
            list_id=item.get("list_id") or "",
        )
        for item in raw["fields"]
        if item["label"]
    ]
    _assign_groups(fields)
    buttons = [
        ExtractedButton(
            uid=item["uid"],
            text=item["text"],
            button_type=item["type"],
            onclick=item["onclick"],
            heading=item["heading"],
            in_dialog=bool(item["in_dialog"]),
        )
        for item in raw["buttons"]
    ]
    return PageSnapshot(
        fields=fields,
        buttons=buttons,
        progress=raw["progress"],
        alerts=list(raw["alerts"]),
        gate=raw.get("gate") or "",
    )


_EDUCATION_LABEL = re.compile(r"^(school|university|college|institution|degree|discipline|major|field of study)\b", re.I)
_WORK_LABEL = re.compile(r"^(company|employer|organi[sz]ation|job title|title)\b", re.I)
_DATE_PART_LABEL = re.compile(r"^(start|end) date (year|month)$", re.I)


def _assign_groups(fields: list[ExtractedField]) -> None:
    seen: dict[tuple[str, str], int] = {}
    context = ""
    for item in fields:
        item.section = section_of(item.heading)
        # Some boards put education and employment under one heading. The fields just above say which block a date is in.
        if _EDUCATION_LABEL.match(item.label.strip()):
            context = "education"
        elif _WORK_LABEL.match(item.label.strip()):
            context = "work"
        if not item.section and context and _DATE_PART_LABEL.match(item.label.strip()):
            item.section = context
        label = re.sub(r"\s+", " ", item.label).strip().casefold()
        key = (item.section, label)
        item.group = seen.get(key, 0)
        seen[key] = item.group + 1
