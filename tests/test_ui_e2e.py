"""Drive the desk against three fictional forms. The filler browser stays headless."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from pocket_agent.forms.catalog import FORMS
from pocket_agent.forms.hard_catalog import HARD_FORMS
from pocket_agent.forms.server import FormServer
from pocket_agent.ui.app import SettingsIn, create_app, save_settings

ARTIFACTS = Path(__file__).resolve().parents[1] / ".pytest_cache" / "ui-artifacts"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(port: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("ui did not start")


def _answer_bubbles(page, shots: dict) -> None:
    for _ in range(8):
        page.wait_for_function(
            """() => {
              const review = document.querySelector('#review');
              if (review && !review.hidden) return true;
              return [...document.querySelectorAll('.bubble')].some((bubble) => bubble.querySelector('button:not([disabled])'));
            }""",
            timeout=45000,
        )
        if page.locator("#review").is_visible() and page.locator(".bubble button:not([disabled])").count() == 0:
            return
        bubble = page.locator(".bubble").filter(has=page.locator("button:not([disabled])")).last
        if not shots.get("question"):
            page.screenshot(path=str(ARTIFACTS / "ui-question.png"))
            shots["question"] = True
        if bubble.locator(".choice").count():
            bubble.locator(".choice").first.click()
        else:
            field = bubble.locator("select, textarea, input:not([type=checkbox])").first
            kind = field.evaluate("el => (el.tagName + '/' + (el.type || '')).toLowerCase()")
            if kind.startswith("select"):
                labels = field.locator("option").all_text_contents()
                field.select_option("Referral" if "Referral" in labels else labels[0])
            elif "date" in kind:
                field.fill("2026-06-15")
            elif "textarea" in kind:
                field.fill("A short fictional note.")
            else:
                field.fill("80000" if "salary" in bubble.inner_text().lower() else "Sam Rivera")
            bubble.locator("#send-answer").click()
        page.wait_for_timeout(200)
    page.locator("#review").wait_for(timeout=20000)


def test_three_forms_through_the_desk(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POCKET_AGENT_HOME", str(tmp_path))
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    forms = FormServer(tuple(FORMS) + tuple(HARD_FORMS))
    forms.start()
    port = _free_port()
    app = create_app()
    save_settings(SettingsIn(model="none", headless=True))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_http(port)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/")
            page.locator("#url").wait_for()
            page.screenshot(path=str(ARTIFACTS / "ui-home.png"))
            page.locator("#headless").check()

            page.locator("#url").fill(forms.url("gh-amber"))
            page.locator("#start").click()
            page.locator("#review-message").wait_for(timeout=60000)
            assert page.locator("#review-message").inner_text() == "Review the form in the browser window and click Submit yourself."
            assert page.locator("#review").get_attribute("data-submits") == "0"
            page.screenshot(path=str(ARTIFACTS / "ui-review.png"))

            shots: dict[str, bool] = {}
            page.locator("#url").fill(forms.url("hd-questions"))
            page.locator("#start").click()
            _answer_bubbles(page, shots)
            page.locator("#review-message").wait_for(timeout=60000)
            assert page.locator("#review").get_attribute("data-submits") == "0"
            assert shots.get("question")

            page.locator("#url").fill(forms.url("hd-login"))
            page.locator("#start").click()
            page.locator("#gate").wait_for(timeout=30000)
            assert "Sign in in the browser window" in page.locator("#gate-copy").inner_text()
            assert page.locator("#continue").is_visible()

            page.locator("nav button[data-screen=answers]").click()
            page.locator("#memory-list").wait_for()
            page.locator("#memory-list").scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS / "ui-answers.png"), full_page=True)
            text = page.locator("#memory-list").inner_text()
            assert "how_did_you_hear" in text
            browser.close()
    finally:
        server.should_exit = True
        forms.stop()
