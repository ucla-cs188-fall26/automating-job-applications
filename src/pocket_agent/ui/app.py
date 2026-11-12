"""Localhost desk: chat, questions, memory, and settings. No accounts and no cloud."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pocket_agent.cli import _copy_user_files
from pocket_agent.forms.llm import MODEL_FILES, model_path
from pocket_agent.forms.memory import Memory
from pocket_agent.paths import data_home
from pocket_agent.profile import dump_profile, load_profile
from pocket_agent.resume import parse_resume

STATIC = Path(__file__).resolve().parent / "static"


class SettingsIn(BaseModel):
    model: str = "0.6b"
    headless: bool = False


class MemoryIn(BaseModel):
    value: str
    always_use: bool = False


class ProfileIn(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    region: str = ""


def _settings_path() -> Path:
    return data_home() / "ui-settings.json"


def load_settings() -> dict:
    raw = {"model": "0.6b", "headless": False}
    path = _settings_path()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict):
            raw.update({key: stored[key] for key in ("model", "headless") if key in stored})
    if raw["model"] not in {"none", "0.6b", "1.7b", "4b"}:
        raw["model"] = "0.6b"
    raw["headless"] = bool(raw["headless"])
    return raw


def save_settings(payload: SettingsIn) -> dict:
    data = {"model": payload.model, "headless": payload.headless}
    if data["model"] not in {"none", "0.6b", "1.7b", "4b"}:
        raise HTTPException(status_code=400, detail="unknown model")
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def model_inventory() -> list[dict]:
    rows = []
    for size, name in MODEL_FILES.items():
        path = model_path(size)
        rows.append({
            "size": size,
            "file": name,
            "present": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
        })
    return rows


def _memory() -> Memory:
    return Memory(data_home() / "forms.sqlite")


def _profile_public() -> dict:
    path = data_home() / "profile.yaml"
    if not path.exists():
        return {"name": "", "email": "", "phone": "", "city": "", "region": ""}
    data = load_profile(path)
    location = data.get("location") or {}
    return {
        "name": str(data.get("name") or ""),
        "email": str(data.get("email") or ""),
        "phone": str(data.get("phone") or ""),
        "city": str(location.get("city") or ""),
        "region": str(location.get("region") or ""),
    }


class Desk:
    """One filler thread. The browser window stays open so the user can submit."""

    def __init__(self) -> None:
        self.commands: queue.Queue = queue.Queue()
        self.answers: queue.Queue = queue.Queue()
        self.pending: queue.Queue = queue.Queue()
        self.async_q: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self._headless: bool | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.async_q = asyncio.Queue()
        while not self.pending.empty():
            self.async_q.put_nowait(self.pending.get_nowait())

    def publish(self, event: dict) -> None:
        if self.loop is None or self.async_q is None:
            self.pending.put(event)
            return
        self.loop.call_soon_threadsafe(self.async_q.put_nowait, event)

    def handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "answer":
            self.answers.put({"value": str(message.get("value") or ""), "remember": bool(message.get("remember"))})
            return
        if kind == "start":
            url = str(message.get("url") or "")
            if not url.startswith(("http://", "https://")):
                self.publish({"type": "error", "message": "Paste a full http or https link."})
                return
        if kind in {"start", "continue", "stop"}:
            self._ensure_worker()
            self.commands.put(message)
            return
        self.publish({"type": "error", "message": "unknown message"})

    def _ensure_worker(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._worker, name="pocket-ui-fill", daemon=True)
        self.thread.start()

    def _worker(self) -> None:
        from playwright.sync_api import sync_playwright

        from pocket_agent.forms.engine import Asked, UserAnswer, fill_form
        from pocket_agent.forms.llm import LocalDecider
        from pocket_agent.forms.seed import seed_memory

        try:
            with sync_playwright() as playwright:
                browser = None
                page = None
                while True:
                    command = self.commands.get()
                    if command.get("type") == "stop":
                        break
                    settings = load_settings()
                    headless = bool(command.get("headless", settings["headless"]))
                    if browser is None or headless != self._headless:
                        if browser is not None:
                            browser.close()
                        browser = playwright.chromium.launch(headless=headless)
                        page = browser.new_page()
                        self._headless = headless
                    assert page is not None
                    self.running = True
                    forget: list[str] = []
                    memory = _memory()
                    try:
                        home = data_home()
                        profile = load_profile(home / "profile.yaml")
                        resume = home / "resume.pdf"
                        seed_memory(memory, profile, resume_path=str(resume) if resume.exists() else "")
                        model_name = str(command.get("model") or settings["model"])
                        decider = None
                        if model_name != "none":
                            decider = LocalDecider(model_name)
                            if not decider.available:
                                self.publish({"type": "status", "text": f"Model file missing ({decider.error}). Filling from the profile only."})
                                decider = None

                        def ask(asked: Asked) -> UserAnswer:
                            self.publish({
                                "type": "ask",
                                "key": asked.key,
                                "label": asked.label,
                                "question": asked.question,
                                "kind": asked.kind,
                                "options": list(asked.options),
                                "sensitive": asked.sensitive,
                                "section": asked.section,
                                "remember": not asked.sensitive,
                            })
                            reply = self.answers.get()
                            remember = bool(reply.get("remember"))
                            if not remember:
                                forget.append(asked.key)
                            return UserAnswer(str(reply.get("value") or ""), always_use=remember)

                        url = str(command.get("url") or "")
                        if command.get("type") == "start" and not url.startswith(("http://", "https://")):
                            self.publish({"type": "error", "message": "Paste a full http or https link."})
                            continue
                        self.publish({"type": "status", "text": "Filling. Nothing is submitted."})
                        run = fill_form(
                            page,
                            url,
                            memory,
                            resume if resume.exists() else None,
                            ask,
                            decider,
                            events=self.publish,
                            navigate=command.get("type") != "continue",
                        )
                        if run.error:
                            self.publish({"type": "error", "message": run.error})
                    except Exception as exc:  # noqa: BLE001 — surface filler failures on the desk
                        self.publish({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                    finally:
                        for key in forget:
                            memory.delete(key)
                        memory.close()
                        self.running = False
                if browser is not None:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            self.publish({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


def create_app() -> FastAPI:
    _copy_user_files(data_home())
    desk = Desk()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        desk.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Pocket Agent", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.desk = desk

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/settings")
    def get_settings() -> dict:
        settings = load_settings()
        return {**settings, "models": model_inventory()}

    @app.put("/api/settings")
    def put_settings(payload: SettingsIn) -> dict:
        saved = save_settings(payload)
        return {**saved, "models": model_inventory()}

    @app.get("/api/memory")
    def get_memory() -> dict:
        memory = _memory()
        try:
            entries = [
                {
                    "key": item.key,
                    "value": item.value,
                    "always_use": item.always_use,
                    "sensitive": item.sensitive,
                    "source": item.source,
                }
                for item in memory.all_entries()
            ]
        finally:
            memory.close()
        return {"entries": entries}

    @app.put("/api/memory/{key}")
    def put_memory(key: str, payload: MemoryIn) -> dict:
        if desk.running:
            raise HTTPException(status_code=409, detail="Wait until the current form finishes.")
        memory = _memory()
        try:
            current = memory.get(key)
            memory.put(
                key,
                payload.value,
                always_use=payload.always_use,
                sensitive=current.sensitive if current else False,
                source=current.source if current else "answer",
            )
        finally:
            memory.close()
        return {"ok": True}

    @app.delete("/api/memory/{key}")
    def delete_memory(key: str) -> dict:
        if desk.running:
            raise HTTPException(status_code=409, detail="Wait until the current form finishes.")
        memory = _memory()
        try:
            memory.delete(key)
        finally:
            memory.close()
        return {"ok": True}

    @app.get("/api/profile")
    def get_profile() -> dict:
        return _profile_public()

    @app.put("/api/profile")
    def put_profile(payload: ProfileIn) -> dict:
        if desk.running:
            raise HTTPException(status_code=409, detail="Wait until the current form finishes.")
        path = data_home() / "profile.yaml"
        data = load_profile(path)
        data["name"] = payload.name.strip() or data.get("name")
        data["email"] = payload.email.strip()
        data["phone"] = payload.phone.strip()
        location = dict(data.get("location") or {})
        location["city"] = payload.city.strip()
        location["region"] = payload.region.strip()
        data["location"] = location
        dump_profile(path, data)
        memory = _memory()
        try:
            from pocket_agent.forms.seed import seed_memory

            resume = data_home() / "resume.pdf"
            seed_memory(memory, data, resume_path=str(resume) if resume.exists() else "")
        finally:
            memory.close()
        return _profile_public()

    async def upload_resume(file: UploadFile) -> dict:
        if desk.running:
            raise HTTPException(status_code=409, detail="Wait until the current form finishes.")
        dest = data_home() / "resume.pdf"
        dest.write_bytes(await file.read())
        parsed = parse_resume(dest)
        data = load_profile(data_home() / "profile.yaml")
        memory = _memory()
        try:
            from pocket_agent.forms.seed import seed_memory

            seed_memory(memory, data, resume_path=str(dest))
        finally:
            memory.close()
        return {"ok": True, "lines": parsed.get("lines") or [], "emails": parsed.get("emails") or []}

    app.add_api_route("/api/resume", upload_resume, methods=["POST"])

    @app.get("/api/resume")
    def read_resume() -> dict:
        parsed = parse_resume(home=data_home())
        return {"ok": bool(parsed.get("ok")), "lines": parsed.get("lines") or [], "emails": parsed.get("emails") or []}

    @app.websocket("/ws")
    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()

        async def pump() -> None:
            assert desk.async_q is not None
            while True:
                event = await desk.async_q.get()
                await websocket.send_json(event)

        sender = asyncio.create_task(pump())
        try:
            while True:
                message = await websocket.receive_json()
                desk.handle(message)
        except Exception:
            sender.cancel()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
