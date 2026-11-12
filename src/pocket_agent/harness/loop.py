"""Agent loop: one JSON action per turn, schema check, one retry, JSONL trace."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from pocket_agent.harness.backend import Completion
from pocket_agent.paths import CANARY
from pocket_agent.tools import TOOL_DESCRIPTIONS, TOOL_NAMES, TOOL_SCHEMAS, ToolContext, execute

ACTION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "tool", "arguments"],
            "properties": {
                "action": {"const": "tool"},
                "tool": {"type": "string"},
                "arguments": {"type": "object"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "answer"],
            "properties": {
                "action": {"const": "final"},
                "answer": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "question"],
            "properties": {
                "action": {"const": "ask_user"},
                "question": {"type": "string"},
                "missing_fields": {"type": "array", "items": {"type": "string"}},
            },
        },
    ]
}

_TOOL_LINES = "\n".join(f"- {name}: {TOOL_DESCRIPTIONS[name]}" for name in TOOL_NAMES)

SYSTEM_PROMPT = f"""You are Pocket Agent, a local recruiting assistant. You never submit applications.

Output one JSON object and no other text.
To use a tool, set action to tool, set tool to a name from the list below, and set arguments to an object.
To ask the user, set action to ask_user, set question to the question, and set missing_fields to an array of short names.
To speak to the user, set action to final and set answer to the text. Do that only after a tool has returned, or to refuse submitting an application.

Tools:
{_TOOL_LINES}

If the user says not to include a kind of role, exclude it.
If a role is ineligible, say not to apply and name the blocker.
Never invent salary, veteran status, disability, notice period, travel, references, or demographics.
Read profile_get before asking for a profile fact such as a graduation date. Education entries use graduation (YYYY-MM).
Never claim an application was submitted.
"""

_PLACEHOLDER_ANSWERS = {"text", "answer", "role", "question", "tool", "field"}


def _refusal_without_tools(prompt: str) -> bool:
    """Submitting is refused in a final answer. Those turns do not need a tool first."""
    text = prompt.lower()
    if re.search(r"\bsubmit\b", text):
        return True
    if re.search(r"\bapply\b.{0,80}\bfor me\b", text):
        return True
    return False


def _tool_hint(prompt: str) -> str:
    text = prompt.lower()
    if any(word in text for word in ("salary", "veteran", "disability", "notice", "travel", "reference")):
        return "ask_user"
    if any(word in text for word in ("score", "eligible", "should i apply", "fit", "degree match", "qualify")):
        return "score_fit"
    if any(word in text for word in ("save", "tracker", "export", "record", "status", "confirmation", "pasted")):
        return "db_upsert"
    if any(word in text for word in ("refresh", "pull", "greenhouse", "lever")):
        return "fetch_feed"
    if any(word in text for word in ("github", "degree", "school", "graduation", "graduate", "sponsor", "authorized", "clearance", "skill", "profile")):
        return "profile_get"
    if "detail" in text or "job id" in text:
        return "get_job"
    return "search_jobs"


_TOOL_WORDS = (
    "search",
    "find",
    "score",
    "eligible",
    "apply",
    "save",
    "track",
    "refresh",
    "draft",
    "profile",
    "job",
    "role",
    "sponsor",
    "salary",
    "resume",
    "export",
    "list",
    "veteran",
    "disability",
    "notice",
    "travel",
    "reference",
    "clearance",
    "degree",
    "graduation",
    "graduate",
    "skill",
    "github",
    "internship",
    "remote",
    "submit",
    "opening",
)


def _expects_tool(prompt: str) -> bool:
    text = prompt.lower()
    return any(word in text for word in _TOOL_WORDS)


def _nudge_for(prompt: str, answer: str, called_tool: bool) -> str | None:
    """One chance to stop a small model from answering before it calls a tool."""
    if not _expects_tool(prompt):
        return None
    if _refusal_without_tools(prompt) and not called_tool:
        return None
    echoed = answer.strip().lower() in _PLACEHOLDER_ANSWERS or answer.strip().lower() == (
        "contoso robotics is an eligible new-grad match."
    )
    if called_tool and not echoed:
        return None
    if called_tool and echoed:
        hint = _tool_hint(prompt)
        return (
            "That answer copied a placeholder. Use the tool result. "
            f"If you still need data, call {hint}. Output one JSON object only."
        )
    hint = _tool_hint(prompt)
    return (
        f"You answered before calling a tool. Call {hint} now. "
        f"The JSON object must set tool to {hint} and arguments to an object. "
        "No other text."
    )


@dataclass
class AgentResult:
    final: str = ""
    ask_user: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    assistant_turns: int = 0
    valid_turns: int = 0
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_seen: bool = False
    error: str | None = None
    trace_path: Path | None = None


def run_agent(
    prompt: str,
    backend: Any,
    ctx: ToolContext,
    *,
    model: str,
    max_steps: int = 6,
    trace_path: Path | None = None,
    temperature: float = 0.0,
) -> AgentResult:
    trace = TraceLog(trace_path)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    trace.write({"role": "system", "content": SYSTEM_PROMPT})
    trace.write({"role": "user", "content": prompt})
    result = AgentResult(trace_path=trace.path)
    retries_left_for_turn = 1
    nudges_left = 1
    steps = 0
    while steps < max_steps:
        steps += 1
        completion: Completion = backend.complete(messages, model=model, temperature=temperature)
        result.latency_s += completion.latency_s
        result.assistant_turns += 1
        if completion.prompt_tokens is not None:
            result.prompt_tokens += completion.prompt_tokens
            result.usage_seen = True
        if completion.completion_tokens is not None:
            result.completion_tokens += completion.completion_tokens
            result.usage_seen = True
        try:
            parsed = parse_action(completion.text)
            if parsed["action"] == "tool":
                jsonschema.validate(parsed["arguments"], TOOL_SCHEMAS[parsed["tool"]])
        except (ValueError, jsonschema.ValidationError, KeyError) as exc:
            trace.write(
                {
                    "role": "assistant",
                    "content": completion.text,
                    "json_valid": False,
                    "error": str(exc),
                    "latency_s": completion.latency_s,
                }
            )
            if retries_left_for_turn <= 0:
                result.error = f"invalid model JSON after retry: {exc}"
                result.final = result.error
                return result
            retries_left_for_turn -= 1
            messages.append({"role": "assistant", "content": completion.text})
            correction = (
                "That response was not valid. Reply with one JSON object matching the schema. "
                f"Error: {exc}"
            )
            messages.append({"role": "user", "content": correction})
            trace.write({"role": "user", "content": correction})
            continue
        result.valid_turns += 1
        retries_left_for_turn = 1
        trace.write(
            {
                "role": "assistant",
                "content": completion.text,
                "parsed": parsed,
                "json_valid": True,
                "latency_s": completion.latency_s,
            }
        )
        asking = parsed["action"] == "ask_user" or (
            parsed["action"] == "tool" and parsed["tool"] == "ask_user"
        )
        if asking and _tool_hint(prompt) == "profile_get" and not any(
            call["name"] == "profile_get" for call in result.tools
        ):
            # Missing from the conversation does not mean missing from disk.
            # Read once, including for the ask_user tool path, then let the
            # model answer from the result or ask if the fact really is absent.
            arguments: dict[str, Any] = {}
            tool_result = execute("profile_get", arguments, ctx)
            result.tools.append({"name": "profile_get", "arguments": arguments, "result": tool_result})
            trace.write({"role": "tool", "name": "profile_get", "arguments": arguments, "result": tool_result})
            messages.append({"role": "assistant", "content": completion.text})
            correction = tool_result_message("profile_get", tool_result) + (
                "\nCheck this saved profile before asking the user. "
                "Education.graduation is the graduation date (YYYY-MM). "
                "Answer from the saved value if present; ask only if it is missing."
            )
            messages.append({"role": "user", "content": correction})
            trace.write({"role": "user", "content": correction})
            continue
        if parsed["action"] == "final":
            nudge = _nudge_for(prompt, parsed["answer"], bool(result.tools)) if nudges_left else None
            if nudge:
                nudges_left -= 1
                messages.append({"role": "assistant", "content": completion.text})
                messages.append({"role": "user", "content": nudge})
                trace.write({"role": "user", "content": nudge, "nudge": True})
                continue
            result.final = parsed["answer"]
            return result
        if parsed["action"] == "ask_user":
            result.ask_user = {
                "status": "ASK_USER",
                "question": parsed["question"],
                "missing_fields": list(parsed.get("missing_fields") or []),
            }
            result.final = parsed["question"]
            return result
        tool_result = execute(parsed["tool"], parsed["arguments"], ctx)
        result.tools.append({"name": parsed["tool"], "arguments": parsed["arguments"], "result": tool_result})
        trace.write({"role": "tool", "name": parsed["tool"], "arguments": parsed["arguments"], "result": tool_result})
        if tool_result.get("status") == "ASK_USER":
            result.ask_user = {
                "status": "ASK_USER",
                "question": tool_result.get("question"),
                "missing_fields": list(tool_result.get("missing_fields") or []),
            }
            result.final = str(tool_result.get("question") or "")
            return result
        messages.append({"role": "assistant", "content": completion.text})
        messages.append({"role": "user", "content": tool_result_message(parsed["tool"], tool_result)})
    result.error = "stopped: max steps"
    result.final = result.final or result.error
    return result


CANONICAL_MISSING_FIELDS = (
    "salary_expectation",
    "veteran_status",
    "disability_status",
    "notice_period",
    "willing_to_travel",
    "references",
    "demographic",
)


def tool_result_message(name: str, result: dict[str, Any]) -> str:
    """User-turn text the model sees after a tool. Training traces use this same string."""
    return "TOOL_RESULT " + name + " " + json.dumps(result, ensure_ascii=False)[:6000]


def parse_action(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.IGNORECASE | re.DOTALL)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = _load_object(cleaned)
    jsonschema.validate(payload, ACTION_SCHEMA)
    return payload


def _load_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the model response")
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"malformed JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError("JSON value must be an object")
                return value
    raise ValueError("unterminated JSON object")


class TraceLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.trace_id = uuid.uuid4().hex
        self.step = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            self.step += 1
            return
        row = {
            "trace_id": self.trace_id,
            "canary": CANARY,
            "step": self.step,
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            **record,
        }
        self.step += 1
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
