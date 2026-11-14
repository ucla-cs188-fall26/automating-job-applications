"""pocket-agent command line."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pocket_agent import __version__
from pocket_agent.config import load_config, save_config
from pocket_agent.eval.runner import format_report, run_eval, write_report
from pocket_agent.feeds.cache import CachedHttp
from pocket_agent.feeds.service import fetch_feed
from pocket_agent.hardware import detect_hardware
from pocket_agent.harness.backend import backend_for
from pocket_agent.harness.loop import run_agent
from pocket_agent.mcp_server import serve
from pocket_agent.models import TIERS
from pocket_agent.paths import data_home, examples_dir
from pocket_agent.profile import example_profile_path, load_profile, public_view
from pocket_agent.resume import parse_resume
from pocket_agent.resume_pdf import write_simple_pdf
from pocket_agent.scoring import score_fit
from pocket_agent.snapshot import load_snapshot, save_snapshot
from pocket_agent.synthetic import write_profiles
from pocket_agent.teacher import collect_traces, teacher_settings_from_env
from pocket_agent.tools import TOOL_NAMES, ToolContext, execute
from pocket_agent.tracker import Tracker

RESUME_LINES = [
    "Jordan Hale",
    "Austin, TX | jordan.hale@example.com | +1-555-0100",
    "Fictional resume shipped with Pocket Agent. Not a real person.",
    "Education",
    "Northwind University, B.S. Computer Science, May 2026, GPA 3.6",
    "Experience",
    "Software Engineering Intern, Contoso Robotics, May 2025 - Aug 2025",
    "Built an internal Python service for sensor logs.",
    "Skills",
    "Python, TypeScript, SQL, FastAPI, React",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pocket-agent", description="Local recruiting assistant. Never submits applications.")
    parser.add_argument("--version", action="version", version=f"pocket-agent {__version__}")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Detect hardware, create ~/.pocket-agent, optionally pull a model")
    init.add_argument("--model", help="Override the tier's Ollama tag")
    init.add_argument("--no-pull", action="store_true", help="Skip ollama pull")
    init.add_argument("--yes", action="store_true", help="Non-interactive")
    init.set_defaults(func=cmd_init)

    hardware = sub.add_parser("hardware", help="Print OS, RAM, and the selected model tier")
    hardware.set_defaults(func=cmd_hardware)

    chat = sub.add_parser("chat", help="Run one agent turn, or several if a terminal is attached")
    chat.add_argument("prompt", nargs="?")
    chat.add_argument("--backend", choices=["ollama", "llamacpp", "mlx", "mock"])
    chat.add_argument("--model")
    chat.add_argument("--base-url")
    chat.set_defaults(func=cmd_chat)

    fetch = sub.add_parser("fetch", help="Refresh feeds into the local tracker")
    fetch.add_argument("--source", default="simplify", help="simplify (default), new-grad, intern, greenhouse, lever")
    fetch.add_argument("--company", help="Required for greenhouse and lever")
    fetch.add_argument("--limit", type=int)
    fetch.set_defaults(func=cmd_fetch)

    search = sub.add_parser("search", help="Search the local tracker")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--location", default="")
    search.add_argument("--company", default="")
    search.add_argument("--source", default="")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--remote-only", action="store_true")
    search.set_defaults(func=cmd_search)

    show = sub.add_parser("job", help="Show one stored job")
    show.add_argument("job_id")
    show.set_defaults(func=cmd_job)

    score = sub.add_parser("score", help="Score one job against the profile")
    score.add_argument("job_id")
    score.set_defaults(func=cmd_score)

    track = sub.add_parser("track", help="List, update, or export applications")
    track_sub = track.add_subparsers(dest="track_cmd")
    add = track_sub.add_parser("add")
    add.add_argument("--job-id", required=True)
    add.add_argument("--status", default="saved")
    add.add_argument("--notes", default="")
    add.set_defaults(func=cmd_track_add)
    update = track_sub.add_parser("update")
    update.add_argument("--job-id", required=True)
    update.add_argument("--text", help="Confirmation text to infer a status from")
    update.add_argument("--status")
    update.set_defaults(func=cmd_track_update)
    listing = track_sub.add_parser("list")
    listing.add_argument("--status")
    listing.set_defaults(func=cmd_track_list)
    export = track_sub.add_parser("export")
    export.add_argument("path", type=Path)
    export.set_defaults(func=cmd_track_export)
    track.set_defaults(func=cmd_track_list)

    profile = sub.add_parser("profile", help="Show the local profile")
    profile.add_argument("--section")
    profile.set_defaults(func=cmd_profile)

    resume = sub.add_parser("resume", help="Parse the local resume")
    resume.add_argument("path", nargs="?", type=Path)
    resume.set_defaults(func=cmd_resume)

    snap = sub.add_parser("snapshot", help="Save, load, or freeze a postings snapshot")
    snap_sub = snap.add_subparsers(dest="snap_cmd", required=True)
    save = snap_sub.add_parser("save")
    save.add_argument("path", type=Path)
    save.set_defaults(func=cmd_snapshot_save)
    load = snap_sub.add_parser("load")
    load.add_argument("path", type=Path)
    load.set_defaults(func=cmd_snapshot_load)
    freeze = snap_sub.add_parser("freeze")
    freeze.add_argument("path", type=Path)
    freeze.add_argument("--limit-each", type=int, default=1000, help="Active Simplify rows to keep from each list")
    freeze.set_defaults(func=cmd_snapshot_freeze)

    synth = sub.add_parser("synth", help="Write fictional profiles (Week 2 seed data)")
    synth.add_argument("--count", type=int, default=300)
    synth.add_argument("--out", type=Path, required=True)
    synth.add_argument("--seed", type=int, default=428)
    synth.set_defaults(func=cmd_synth)

    evaluate = sub.add_parser("eval", help="Run the hand-checked task suite")
    evaluate.add_argument("--suite", choices=["week1", "smoke", "heldout"], default="week1")
    evaluate.add_argument("--backend", choices=["mock", "ollama", "llamacpp", "mlx", "transformers"], default="mock")
    evaluate.add_argument("--model", default="mock")
    evaluate.add_argument("--adapter", help="LoRA adapter directory for the transformers backend")
    evaluate.add_argument("--base-url")
    evaluate.add_argument("--limit", type=int)
    evaluate.add_argument("--fail-under", type=float)
    evaluate.add_argument("--out", type=Path)
    evaluate.add_argument("--label")
    evaluate.set_defaults(func=cmd_eval)

    fill = sub.add_parser("fill", help="Fill an application form in the browser and stop before submit")
    fill.add_argument("url")
    fill.add_argument("--model", default="gpt", choices=["none", "gpt", "claude"], help="Hosted AI provider (API key and POCKET_AGENT_AI_MODEL required)")
    fill.add_argument("--resume", type=Path)
    fill.add_argument("--workday-accounts", action="store_true", help="Create/sign in to Workday accounts using macOS Keychain")
    mode = fill.add_mutually_exclusive_group()
    mode.add_argument("--unattended", dest="unattended", action="store_true", default=None, help="Never prompt in the terminal; use saved answers and report unresolved fields")
    mode.add_argument("--interactive", dest="unattended", action="store_false", help="Ask for missing answers, overriding the saved unattended setting")
    fill.add_argument("--headless", action="store_true", help="Run without a visible browser")
    fill.add_argument("--close-on-finish", action="store_true", help="Close the browser after writing the result")
    fill.set_defaults(func=cmd_fill)

    ui = sub.add_parser("ui", help="Open the local form desk in a browser")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-open", action="store_true", help="Do not open a browser window")
    ui.set_defaults(func=cmd_ui)

    eval_forms = sub.add_parser("eval-forms", help="Score the local fictional application suite")
    eval_forms.add_argument("--suite", choices=["easy", "hard", "both"], default="easy")
    eval_forms.add_argument("--runs", type=int, default=1)
    eval_forms.add_argument("--model", default="none", choices=["none", "0.6b", "1.7b", "4b"])
    eval_forms.add_argument("--no-perturb", action="store_true")
    eval_forms.add_argument("--out", type=Path)
    eval_forms.set_defaults(func=cmd_eval_forms)

    teach = sub.add_parser("teach", help="Run the harness on a teacher model and keep traces that pass")
    teach.add_argument("--suite", choices=["week1", "smoke", "heldout"], default="smoke")
    teach.add_argument("--limit", type=int)
    teach.add_argument("--out", type=Path, required=True)
    teach.set_defaults(func=cmd_teach)

    sub.add_parser("mcp", help="Serve tools over MCP stdio").set_defaults(func=cmd_mcp)
    sub.add_parser("tools", help="List tool names").set_defaults(func=cmd_tools)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    hardware = detect_hardware(args.model)
    _copy_user_files(home)
    Tracker(home / "tracker.db").close()
    base_urls = {
        "ollama": "http://127.0.0.1:11434/v1",
        "llamacpp": "http://127.0.0.1:8080/v1",
        "mlx": "http://127.0.0.1:8080/v1",
    }
    config = {
        "backend": "ollama",
        "base_url": base_urls["ollama"],
        "model": hardware.model.tag,
        "tier": hardware.tier,
        "os": hardware.os_name,
        "ram_gb": None if hardware.ram_gb is None else round(hardware.ram_gb, 2),
        "license": hardware.model.license,
        "hf_repo": hardware.model.hf_repo,
    }
    save_config(config, home)
    print(f"Pocket Agent home: {home}")
    print(hardware.summary)
    print(f"License: {hardware.model.license}. {hardware.model.notes}")
    if hardware.model.alternative_tag:
        print(f"Alternative tag: {hardware.model.alternative_tag}")
    if hardware.model.license == "unverified":
        print("This tag was not in the verified Apache-2.0 catalog.")
    if args.no_pull:
        print("Skipped model pull.")
        return 0
    return _pull(hardware.model.tag)


def cmd_hardware(_: argparse.Namespace) -> int:
    hardware = detect_hardware()
    print(hardware.summary)
    for name, spec in TIERS.items():
        marker = " <--" if name == hardware.tier else ""
        print(f"  {name}: {spec.tag} ({spec.license}, {spec.approx_download}){marker}")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    config = load_config()
    backend_name = args.backend or config.get("backend", "ollama")
    model = args.model or config.get("model") or "qwen3:0.6b"
    base = args.base_url or config.get("base_url")
    prompts = []
    if args.prompt:
        prompts.append(args.prompt)
    elif not sys.stdin.isatty():
        prompts.append(sys.stdin.read().strip())
    else:
        print("Pocket Agent. Empty line to exit. Applications are never submitted.")
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                return 0
            if not line:
                return 0
            _chat_once(line, backend_name, model, base)
        return 0
    if not prompts or not prompts[0]:
        print("error: pass a prompt", file=sys.stderr)
        return 2
    return _chat_once(prompts[0], backend_name, model, base)


def _chat_once(prompt: str, backend_name: str, model: str, base: str | None) -> int:
    home = data_home()
    ctx = ToolContext(home=home, network=True)
    client = backend_for(backend_name, base_url=base, model=model)
    trace = home / "traces" / "chat.jsonl"
    result = run_agent(prompt, client, ctx, model=model, trace_path=trace)
    if result.ask_user:
        fields = ", ".join(result.ask_user.get("missing_fields") or [])
        print(f"ASK_USER: {result.ask_user.get('question')}")
        if fields:
            print(f"missing: {fields}")
        return 0
    print(result.final)
    if result.error:
        return 1
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    home = data_home()
    tracker = Tracker(home / "tracker.db")
    http = CachedHttp(home / "cache")
    try:
        result = fetch_feed(tracker, source=args.source, company=args.company, limit=args.limit, network=True, http=http)
    finally:
        http.close()
        tracker.close()
    print(result)
    return 0 if result.get("ok", True) else 1


def cmd_search(args: argparse.Namespace) -> int:
    result = execute(
        "search_jobs",
        {
            "query": args.query,
            "location": args.location,
            "company": args.company,
            "source": args.source,
            "limit": args.limit,
            "remote_only": args.remote_only,
        },
        ToolContext(),
    )
    for job in result.get("jobs") or []:
        print(f"{job['id']}\t{job['company']}\t{job['title']}\t{job['location']}")
    print(f"{result.get('count', 0)} jobs")
    return 0


def cmd_job(args: argparse.Namespace) -> int:
    result = execute("get_job", {"job_id": args.job_id}, ToolContext())
    if not result.get("ok"):
        print(result["error"], file=sys.stderr)
        return 1
    job = result["job"]
    print(f"{job['title']} — {job['company']}")
    print(job.get("location") or "")
    print(job.get("url") or "")
    print(job.get("description") or "")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    ctx = ToolContext()
    tracker = ctx.tracker()
    try:
        job = tracker.get_job(args.job_id)
    finally:
        tracker.close()
    if job is None:
        print(f"unknown job {args.job_id}", file=sys.stderr)
        return 1
    profile = load_profile(ctx.profile_path())
    result = score_fit(profile, job, today=ctx.clock())
    print(f"eligible={result['eligible']} score={result['score']}")
    for reason in result["reasons"]:
        print(f"- {reason}")
    for blocker in result["blockers"]:
        print(f"! {blocker}")
    return 0


def cmd_track_add(args: argparse.Namespace) -> int:
    result = execute(
        "db_upsert",
        {"kind": "application", "record": {"job_id": args.job_id, "status": args.status, "notes": args.notes}},
        ToolContext(),
    )
    print(result)
    return 0 if result.get("ok") else 1


def cmd_track_update(args: argparse.Namespace) -> int:
    record: dict = {"job_id": args.job_id}
    if args.status:
        record["status"] = args.status
    if args.text:
        record["confirmation_text"] = args.text
    result = execute("db_upsert", {"kind": "application", "record": record}, ToolContext())
    print(result)
    return 0 if result.get("ok") else 1


def cmd_track_list(args: argparse.Namespace) -> int:
    payload: dict = {"kind": "applications"}
    status = getattr(args, "status", None)
    if status:
        payload["status"] = status
    result = execute("db_query", payload, ToolContext())
    for row in result.get("applications") or []:
        print(f"{row['status']}\t{row['company']}\t{row['title']}")
    print(f"{result.get('count', 0)} applications")
    return 0


def cmd_track_export(args: argparse.Namespace) -> int:
    tracker = Tracker(data_home() / "tracker.db")
    try:
        count = tracker.export_csv(args.path)
    finally:
        tracker.close()
    print(f"wrote {count} rows to {args.path}")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    profile = load_profile(data_home() / "profile.yaml")
    view = public_view(profile, args.section)
    print(view)
    return 0 if view.get("ok", True) else 1


def cmd_resume(args: argparse.Namespace) -> int:
    result = parse_resume(args.path, home=data_home())
    if not result.get("ok"):
        print(result["error"], file=sys.stderr)
        return 1
    print(result["text"])
    return 0


def cmd_snapshot_save(args: argparse.Namespace) -> int:
    tracker = Tracker(data_home() / "tracker.db")
    try:
        count = save_snapshot(tracker, args.path)
    finally:
        tracker.close()
    print(f"saved {count} jobs to {args.path}")
    return 0


def cmd_snapshot_load(args: argparse.Namespace) -> int:
    tracker = Tracker(data_home() / "tracker.db")
    try:
        counts = load_snapshot(tracker, args.path)
    finally:
        tracker.close()
    print(counts)
    return 0


def cmd_snapshot_freeze(args: argparse.Namespace) -> int:
    """Download the two Simplify lists and write a trimmed JSONL snapshot."""
    home = data_home()
    work = home / "freeze.db"
    if work.exists():
        work.unlink()
    tracker = Tracker(work)
    http = CachedHttp(home / "cache")
    try:
        newgrad = fetch_feed(tracker, source="new-grad", limit=args.limit_each, network=True, http=http)
        intern = fetch_feed(tracker, source="intern", limit=args.limit_each, network=True, http=http)
        count = save_snapshot(tracker, args.path)
    finally:
        http.close()
        tracker.close()
    print({"new_grad": newgrad, "intern": intern, "snapshot_rows": count, "path": str(args.path)})
    return 0 if newgrad.get("ok") and intern.get("ok") else 1


def cmd_synth(args: argparse.Namespace) -> int:
    count = write_profiles(args.out, count=args.count, seed=args.seed)
    print(f"wrote {count} fictional profiles to {args.out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    model = args.model
    if args.backend != "mock" and model == "mock":
        model = load_config().get("model") or "qwen3:0.6b"
    report = run_eval(
        suite=args.suite,
        backend=args.backend,
        model=model,
        base_url=args.base_url,
        limit=args.limit,
        fail_under=args.fail_under,
        label=args.label,
        adapter=args.adapter,
    )
    print(format_report(report))
    if args.out:
        write_report(report, args.out)
        print(f"wrote {args.out}")
    if report.get("failed_threshold"):
        return 1
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    import uvicorn

    from pocket_agent.ui.app import create_app

    host = "127.0.0.1"
    app = create_app()
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://{host}:{args.port}")).start()
    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    from pocket_agent.forms.engine import Asked, UserAnswer, fill_form
    from pocket_agent.forms.cloud import create_decider
    from pocket_agent.forms.memory import Memory
    from pocket_agent.forms.policy import is_sensitive_key
    from pocket_agent.forms.seed import seed_memory

    if args.unattended is None:
        args.unattended = bool((load_config().get("fill") or {}).get("unattended", False))
    home = data_home()
    profile_path = home / "profile.yaml"
    if not profile_path.exists():
        print("No profile yet. Run `pocket-agent init` first.")
        return 1
    profile = load_profile(profile_path)
    resume = Path(args.resume or load_config().get("resume_path") or (home / "resume.pdf"))
    if resume.exists():
        from pocket_agent.resume import resume_contact_profile
        try:
            profile = resume_contact_profile(profile, resume)
        except Exception:
            print("Could not extract resume contacts. Check that your resume is readable and contains one email address.")
            return 1
    accounts = None
    if args.workday_accounts:
        from getpass import getpass
        from pocket_agent.forms.workday import KeychainCredentials, WorkdayAccounts, account_scope

        if not resume.exists():
            print("Workday account creation needs your resume at ~/.pocket-agent/resume.pdf or --resume PATH.")
            return 1
        email = str(profile.get("email") or "")
        if not email:
            print("No email was found in the resume or profile. Add it to either source and retry.")
            return 1
        if not account_scope(args.url):
            print("--workday-accounts requires an HTTPS Workday career-site URL.")
            return 1
        try:
            password_prompt = (lambda _: "") if args.unattended else getpass
            accounts = WorkdayAccounts(args.url, str(profile.get("email") or ""), KeychainCredentials(), password_prompt, default_password=os.environ.get("POCKET_AGENT_ACCOUNT_PASSWORD"))
        except RuntimeError as exc:
            print(str(exc))
            return 1
    memory = Memory(home / "forms.sqlite")
    seed_memory(memory, profile, resume_path=str(resume) if resume.exists() else "")
    resume_text = parse_resume(resume).get("text", "") if args.model != "none" and resume.exists() else ""
    decider = create_decider(args.model, profile, resume_text)

    unresolved: list[str] = []

    def ask(asked: Asked) -> UserAnswer:
        if args.unattended:
            if asked.label not in unresolved:
                unresolved.append(asked.label)
            return UserAnswer("", skip=True)
        print(asked.question)
        value = input("> (type /skip to leave blank) ").strip()
        if value.casefold() == "/skip":
            return UserAnswer("", skip=True)
        always = False
        if is_sensitive_key(asked.key):
            always = input("Always use this answer on future forms? [y/N] ").strip().lower() == "y"
        return UserAnswer(value, always)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=getattr(args, "headless", False))
        page = browser.new_page()
        resume_file = resume if resume.exists() else None
        runs = [fill_form(page, args.url, memory, resume_file, ask, decider)]
        # The opt-in adapter handles only recognized Workday account controls.
        while (runs[-1].gate or runs[-1].error or runs[-1].status != "review") and not runs[-1].submits and len(runs) <= 20:
            account_message = False
            if accounts is not None and runs[-1].gate and not runs[-1].error:
                try:
                    step = accounts.step(page, runs[-1].gate)
                except Exception as exc:
                    # Never print exceptions that might include account field values.
                    print(f"Workday account handling failed ({type(exc).__name__}). Complete this step in the browser.")
                    account_message = True
                else:
                    if step.message:
                        print(step.message)
                        account_message = True
                    if step.progressed:
                        runs.append(fill_form(page, args.url, memory, resume_file, ask, decider, resume=True))
                        continue
            if not account_message:
                print(runs[-1].error or _GATE_HELP.get(runs[-1].gate, "Application has not reached Review. Check the current page."))
            if args.unattended:
                break
            print("After completing the indicated step, press Enter to continue (or type q to stop).")
            try:
                if input("> ").strip().lower() in {"q", "quit", "stop"}:
                    break
            except EOFError:
                break
            runs.append(fill_form(page, args.url, memory, resume_file, ask, decider, resume=True, preserve_existing=True))
        run = runs[-1]
        run.outcomes = [outcome for item in runs for outcome in item.outcomes]
        run.submits = max(item.submits for item in runs)
        ai_stats = decider.backend.stats() if decider is not None else {"attempted": 0, "responses_received": 0, "completed": 0, "failed": 0}
        print(decider.backend.summary() if decider is not None else "[AI] Disabled. Requests attempted: 0.")
        if args.unattended:
            import json
            report_path = home / "last-fill-report.json"
            report_path.write_text(json.dumps({"url": args.url, "status": run.status, "current_step": run.current_step, "gate": run.gate, "error": run.error, "unresolved": list(dict.fromkeys(unresolved + run.unresolved)), "validation_errors": run.validation_errors, "resume_path": str(resume), "submits": run.submits, "ai": ai_stats}, indent=2))
            print(f"Unattended run {run.status}. Current step: {run.current_step or 'unknown'}. Unresolved fields: {len(unresolved)}. Report: {report_path}")
            if run.error:
                print(run.error)
            for label in unresolved:
                print(f"needs answer: {label}")
            print("Browser left open for review. Close the browser window to exit.")
            if not (getattr(args, "headless", False) or getattr(args, "close_on_finish", False)):
                try:
                    page.wait_for_event("close", timeout=0)
                except Exception:
                    pass
        elif not run.gate and not run.error and not getattr(args, "close_on_finish", False) and not getattr(args, "headless", False):
            print("Stopped before submit. Review the browser window and submit it yourself, then press Enter to close it.")
            try:
                input()
            except EOFError:
                pass
        browser.close()
    memory.close()
    _print_fill(run)
    if args.unattended and (run.status != "review" or run.gate or unresolved) and not run.submits and not run.error:
        return 2
    return 1 if run.submits or run.error else 0


def cmd_eval_forms(args: argparse.Namespace) -> int:
    from pocket_agent.forms.evaluate import render_markdown, run_form_eval, write_reports

    report = run_form_eval(runs=args.runs, perturbations=not args.no_perturb, model=args.model, suite=args.suite)
    print(render_markdown(report))
    if args.out:
        markdown = args.out.with_suffix(".md")
        write_reports(report, args.out, markdown)
        print(f"wrote {args.out} and {markdown}")
    return 0 if report["bar"]["pass"] else 1


_GATE_HELP = {
    "start": "This is the job posting, not the form. In the browser window, click Apply yourself (on Workday, then 'Apply Manually').",
    "login": "Complete this account step in the browser window. Workday account automation is available with --workday-accounts; other login flows are manual.",
    "captcha": "The site is showing a CAPTCHA. Solve it yourself in the browser window.",
    "verification": "Complete email verification or two-factor authentication in the browser window.",
    "consent": "The site is asking you to agree to something (for example a data privacy agreement after the resume upload). Read it and click Agree or Cancel yourself in the browser window.",
}


def _print_fill(run) -> None:
    if run.error:
        print(run.error)
    if run.gate:
        print(f"Stopped at a {run.gate} page: {_GATE_HELP.get(run.gate, '')}")
    print(f"Submit clicks: {run.submits}")
    for outcome in run.outcomes:
        if outcome.action == "fill" and outcome.value:
            print(f"filled  {outcome.label}: {outcome.value}")
        elif outcome.asked:
            print(f"asked   {outcome.label}")
        elif outcome.action == "skip":
            print(f"skipped {outcome.label}")


def cmd_teach(args: argparse.Namespace) -> int:
    settings = teacher_settings_from_env()
    summary = collect_traces(
        suite=args.suite,
        out=args.out,
        limit=args.limit,
        base_url=settings["base_url"],
        model=settings["model"],
        api_key=settings["api_key"],
    )
    print(summary)
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    return serve()


def cmd_tools(_: argparse.Namespace) -> int:
    for name in TOOL_NAMES:
        print(name)
    return 0


def _copy_user_files(home: Path) -> None:
    profile = home / "profile.yaml"
    if not profile.exists():
        profile.write_text(example_profile_path().read_text(encoding="utf-8"), encoding="utf-8")
    resume_txt = home / "resume.txt"
    example_txt = examples_dir() / "resume.txt"
    if not resume_txt.exists() and example_txt.exists():
        shutil.copyfile(example_txt, resume_txt)
    resume_pdf = home / "resume.pdf"
    if not resume_pdf.exists():
        example_pdf = examples_dir() / "resume.pdf"
        if example_pdf.exists():
            shutil.copyfile(example_pdf, resume_pdf)
        else:
            write_simple_pdf(resume_pdf, RESUME_LINES)
    sources = home / "sources.yaml"
    example_sources = examples_dir() / "sources.yaml"
    if not sources.exists() and example_sources.exists():
        shutil.copyfile(example_sources, sources)


def _pull(tag: str) -> int:
    if shutil.which("ollama") is None:
        print("Ollama is not on PATH. Install it from https://ollama.com/download")
        print("Or start llama-server / mlx_lm.server and set backend in config.yaml.")
        print(f"Model tag not pulled: {tag}")
        return 1
    print(f"Pulling {tag}")
    return subprocess.call(["ollama", "pull", tag])


if __name__ == "__main__":
    raise SystemExit(main())
