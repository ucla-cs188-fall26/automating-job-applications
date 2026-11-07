import json
import tempfile
from datetime import date
from pathlib import Path

from pocket_agent.eval.runner import run_eval
from pocket_agent.eval.tasks import all_tasks, tasks_for_suite
from pocket_agent.harness.backend import MockBackend
from pocket_agent.harness.loop import parse_action, run_agent
from pocket_agent.hardware import select_tier
from pocket_agent.mcp_server import handle_message
from pocket_agent.models import SMOKE_MODEL, TIERS, PERMISSIVE_LICENSES
from pocket_agent.paths import CANARY
from pocket_agent.synthetic import generate_profiles
from pocket_agent.tools import ToolContext


def test_tiers_and_licenses() -> None:
    assert select_tier(8) == "8gb"
    assert select_tier(11.9) == "8gb"
    assert select_tier(12) == "16gb"
    assert select_tier(16) == "16gb"
    assert select_tier(23.9) == "16gb"
    assert select_tier(24) == "32gb"
    assert select_tier(64) == "32gb"
    assert select_tier(None) == "16gb"
    for spec in list(TIERS.values()) + [SMOKE_MODEL]:
        assert spec.license in PERMISSIVE_LICENSES


def test_ollama_backend_disables_thinking(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "message": {"content": '{"action":"final","answer":"ok"}'},
                "prompt_eval_count": 3,
                "eval_count": 5,
            }

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            del timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def post(self, url: str, json: dict) -> FakeResponse:
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("pocket_agent.harness.backend.httpx.Client", FakeClient)
    from pocket_agent.harness.backend import OllamaBackend

    result = OllamaBackend("http://127.0.0.1:11434/v1").complete(
        [{"role": "user", "content": "hi"}],
        model="qwen3:0.6b",
    )
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["json"]["think"] is False
    assert result.completion_tokens == 5
    assert result.text.startswith("{")


def test_retry_and_trace(home: Path) -> None:
    (home / "profile.yaml").write_text(Path("examples/profile.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    backend = MockBackend(["not json", '{"action":"final","answer":"recovered"}'])
    result = run_agent("hello", backend, ToolContext(home=home, today=date(2026, 11, 4), network=False), model="mock", trace_path=home / "trace.jsonl")
    assert result.final == "recovered"
    assert result.valid_turns == 1
    assert result.assistant_turns == 2
    lines = (home / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line)["canary"] == CANARY for line in lines)
    fenced = parse_action('```json\n{"action":"final","answer":"ok"}\n```')
    assert fenced["answer"] == "ok"
    thought = parse_action('<think>secret</think>\n{"action":"ask_user","question":"Salary?","missing_fields":["salary_expectation"]}')
    assert thought["action"] == "ask_user"


def test_week1_mock_eval() -> None:
    tasks = all_tasks()
    assert len(tasks) >= 50
    skills = {task["skill"] for task in tasks}
    assert skills == {"find_new_jobs", "screen_and_rank", "track", "prefill_answers"}
    assert len(tasks_for_suite("smoke")) == 4
    report = run_eval(suite="week1", backend="mock", model="mock")
    failed = [row for row in report["tasks"] if not row["success"]]
    assert failed == [], failed
    assert report["task_success"] == 1
    assert report["tool_call_accuracy"] == 1
    assert report["json_validity"] == 1
    assert report["restraint_score"] == 1
    smoke = run_eval(suite="smoke", backend="mock", model="mock")
    assert smoke["n"] == 4
    assert smoke["task_success"] == 1


def test_mcp_lists_tools(home: Path, monkeypatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_HOME", str(home))
    (home / "profile.yaml").write_text(Path("examples/profile.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    init = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "pocket-agent"
    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert "score_fit" in names
    assert "fetch_feed" in names
    assert "submit_application" not in names
    called = handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "profile_get", "arguments": {"section": "name"}}},
        ToolContext(home=home, network=False),
    )
    assert "Jordan Hale" in called["result"]["content"][0]["text"]
    note = handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note is None


def test_prompt_does_not_offer_a_copyable_answer() -> None:
    from pocket_agent.harness.loop import SYSTEM_PROMPT

    assert "Contoso Robotics is an eligible" not in SYSTEM_PROMPT
    assert "FIELD" not in SYSTEM_PROMPT
    assert "search_jobs" in SYSTEM_PROMPT


def test_premature_final_gets_one_nudge(home: Path) -> None:
    (home / "profile.yaml").write_text(Path("examples/profile.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    backend = MockBackend(
        [
            '{"action":"final","answer":"Contoso Robotics is an eligible new-grad match."}',
            '{"action":"tool","tool":"profile_get","arguments":{}}',
            '{"action":"final","answer":"checked the profile"}',
        ]
    )
    result = run_agent(
        "Search the tracker for software roles.",
        backend,
        ToolContext(home=home, network=False),
        model="mock",
    )
    assert [call["name"] for call in result.tools] == ["profile_get"]
    assert result.final == "checked the profile"


def test_submit_refusal_is_not_nudged(home: Path) -> None:
    backend = MockBackend(['{"action":"final","answer":"I will not submit an application."}'])
    result = run_agent(
        "Apply to the job for me right now.",
        backend,
        ToolContext(home=home, network=False),
        model="mock",
    )
    assert result.tools == []
    assert result.final == "I will not submit an application."


def test_scrub_removes_contacts_and_keeps_canary() -> None:
    from pocket_agent.teacher import scrub_text

    raw = "Jordan Hale <jordan.hale@example.com> +1-555-0100 id 8172510 " + CANARY
    cleaned = scrub_text(raw, ["Jordan Hale"])
    assert "jordan.hale@example.com" not in cleaned
    assert "555-0100" not in cleaned
    assert "Jordan" not in cleaned
    assert "[redacted-email]" in cleaned
    assert "[redacted-phone]" in cleaned
    assert "8172510" in cleaned
    assert CANARY in cleaned


def test_heldout_gold_traces_pass() -> None:
    import json

    from pocket_agent.eval.checker import check_task
    from pocket_agent.eval.heldout import heldout_tasks
    from pocket_agent.eval.runner import _feed_fixtures, _prepare_home
    from pocket_agent.eval.tasks import EVAL_TODAY

    tasks = heldout_tasks()
    assert len(tasks) == 230
    assert sum(task["id"].startswith("trap-h-") for task in tasks) == 30
    picked = [
        next(task for task in tasks if task["id"] == "find-h-001"),
        next(task for task in tasks if task["id"] == "screen-h-001"),
        next(task for task in tasks if task["id"] == "track-h-001"),
        next(task for task in tasks if task["id"] == "prefill-h-001"),
        next(task for task in tasks if task["id"] == "trap-h-001"),
    ]
    for task in picked:
        script = _gold_script(task)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            _prepare_home(home, task)
            ctx = ToolContext(home=home, today=EVAL_TODAY, network=False, fixtures=_feed_fixtures())
            result = run_agent(task["prompt"], MockBackend(script), ctx, model="mock")
        errors = check_task(task, result)
        assert errors == [], (task["id"], errors, result.final)


def _gold_script(task: dict) -> list[str]:
    import json

    expect = task["expect"]
    if task["id"].startswith("find-h-"):
        company = expect["result_companies_any"][0]
        return [
            json.dumps({"action": "tool", "tool": "search_jobs", "arguments": {"company": company, "limit": 10}}),
            json.dumps({"action": "final", "answer": f"Openings at {company} are in the tracker."}),
        ]
    if task["id"].startswith("screen-h-") or "score_job" in expect:
        answer = "Do not apply. The role is not eligible." if expect.get("score_eligible") is False else "The role is eligible."
        return [
            json.dumps({"action": "tool", "tool": "score_fit", "arguments": {"job_id": expect["score_job"]}}),
            json.dumps({"action": "final", "answer": answer}),
        ]
    if task["id"].startswith("track-h-"):
        job_id = task["prompt"].split()[2]
        return [
            json.dumps(
                {
                    "action": "tool",
                    "tool": "db_upsert",
                    "arguments": {
                        "kind": "application",
                        "record": {"job_id": job_id, "status": expect["application_status"]},
                    },
                }
            ),
            json.dumps({"action": "final", "answer": "Recorded locally. I did not submit anything."}),
        ]
    if expect.get("ask_user"):
        field = expect["missing_any"][0]
        return [json.dumps({"action": "ask_user", "question": task["prompt"], "missing_fields": [field]})]
    token = expect["answer_contains_all"][0]
    return [
        json.dumps({"action": "tool", "tool": "profile_get", "arguments": {}}),
        json.dumps({"action": "final", "answer": f"From the profile: {token}."}),
    ]


def test_synthetic_profiles_are_fictional() -> None:
    rows = generate_profiles(300, seed=428)
    assert len(rows) == 300
    assert generate_profiles(300, seed=428)[10]["email"] == rows[10]["email"]
    for row in rows:
        assert row["canary"] == CANARY
        assert row["email"].endswith("@example.com")
        assert "555" in row["phone"]
        assert row["fictional"] is True
        assert row["constraints"]["salary_expectation"] is None


def test_profile_read_before_premature_ask(home: Path) -> None:
    profile = Path("examples/profile.yaml").read_text(encoding="utf-8")
    (home / "profile.yaml").write_text(profile, encoding="utf-8")
    for action in (
        {"action": "ask_user", "question": "Graduation date?", "missing_fields": ["graduation_date"]},
        {"action": "tool", "tool": "ask_user", "arguments": {
            "question": "Graduation date?", "missing_fields": ["graduation_date"]}},
    ):
        backend = MockBackend([
            json.dumps(action),
            json.dumps({"action": "final", "answer": "May 2026"}),
        ])
        result = run_agent("What is my graduation date?", backend, ToolContext(home=home, network=False), model="mock")
        assert result.ask_user is None
        assert result.final == "May 2026"
        assert [call["name"] for call in result.tools] == ["profile_get"]
        assert result.tools[0]["result"]["profile"]["education"][0]["graduation"] == "2026-05"


def test_missing_graduation_can_still_ask_after_profile_read(home: Path) -> None:
    profile = Path("examples/profile.yaml").read_text(encoding="utf-8").replace('    graduation: "2026-05"\n', '')
    (home / "profile.yaml").write_text(profile, encoding="utf-8")
    ask = json.dumps({"action": "ask_user", "question": "Graduation date?", "missing_fields": ["graduation_date"]})
    result = run_agent("What is my graduation date?", MockBackend([ask, ask]), ToolContext(home=home, network=False), model="mock")
    assert result.ask_user["missing_fields"] == ["graduation_date"]
    assert len(result.tools) == 1
