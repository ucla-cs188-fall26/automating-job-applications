"""Desk API and fill event counts. No browser filler."""

from __future__ import annotations

from fastapi.testclient import TestClient

from pocket_agent.forms.engine import FieldOutcome, activity_source, review_counts
from pocket_agent.forms.memory import MemoryEntry
from pocket_agent.ui.app import create_app


def test_review_counts_keep_asks_out_of_the_filled_total() -> None:
    outcomes = [
        FieldOutcome("Email", "contact", 0, "email", "fill", "a@example.com", False, True),
        FieldOutcome("Salary", "questions", 0, "salary_expectation", "ask", "80000", True, False),
        FieldOutcome("Cover letter", "questions", 0, "cover_letter", "skip", "", False, False),
    ]
    assert review_counts(outcomes) == {"filled": 1, "asked": 1, "skipped": 1}


def test_activity_source_labels() -> None:
    memory = type("M", (), {})()
    memory.get = lambda key: MemoryEntry(key, "x", False, False, "resume") if key == "email" else None
    filled = FieldOutcome("Email", "", 0, "email", "fill", "x", False, True, False)
    modeled = FieldOutcome("City", "", 0, "city", "fill", "Austin", False, True, True)
    asked = FieldOutcome("Salary", "", 0, "salary_expectation", "ask", "1", True, False, False)
    assert activity_source(memory, filled) == "resume"  # type: ignore[arg-type]
    assert activity_source(memory, modeled) == "model"  # type: ignore[arg-type]
    assert activity_source(memory, asked) == "answer"  # type: ignore[arg-type]


def test_settings_profile_and_memory_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        _settings_profile_and_memory(client)


def _settings_profile_and_memory(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert b"click Submit yourself" in page.content
    settings = client.get("/api/settings").json()
    assert settings["model"] == "0.6b"
    assert settings["headless"] is False
    saved = client.put("/api/settings", json={"model": "none", "headless": True}).json()
    assert saved["model"] == "none"
    assert saved["headless"] is True
    created = client.put("/api/memory/how_did_you_hear", json={"value": "Referral", "always_use": False})
    assert created.status_code == 200
    keys = [item["key"] for item in client.get("/api/memory").json()["entries"]]
    assert "how_did_you_hear" in keys
    edited = client.put("/api/profile", json={
        "name": "Jordan Hale",
        "email": "jordan.hale@example.com",
        "phone": "+1-555-0100",
        "city": "Austin",
        "region": "TX",
    })
    assert edited.status_code == 200
    assert edited.json()["city"] == "Austin"
    assert client.delete("/api/memory/how_did_you_hear").status_code == 200
    keys = [item["key"] for item in client.get("/api/memory").json()["entries"]]
    assert "how_did_you_hear" not in keys


def test_websocket_rejects_a_bare_word(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "start", "url": "not a link"})
            message = socket.receive_json()
    assert message["type"] == "error"
    assert "http" in message["message"]
