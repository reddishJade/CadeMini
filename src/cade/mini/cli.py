"""Cade Mini 的命令行入口：单次任务与交互式 REPL。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .compaction import Compactor
from .instructions import load_project_instructions
from .log import SessionLog, list_sessions
from .loop import (
    LoopEvent,
    Provider,
    RunResult,
    TextDeltaEvent,
    Tool,
    ToolStartEvent,
    run,
)
from .policy import WorkspacePolicy
from .projection import project
from .provider import ProviderConfig, build_provider
from .request import Budget
from .tools import build_tools
from .types import LogError, ToolCallBlock
from .window import reset_window

SYSTEM_PROMPT = """\
You are Cade Mini, a coding agent working in a local workspace.
Use the provided tools to inspect and modify files. Read before editing.
Prefer small, verifiable changes. Run relevant checks when useful.
Never claim a change you did not make. Keep answers concise."""

_HELP = """\
Commands:
  /new                 Start a new session
  /resume [ID]         Resume a session (default: latest)
  /compact             Summarize older turns and keep recent work
  /new-context [NOTE]  Start a new window, optionally with a handoff note
  /context             Show session and budget information
  /help                Show this help
  /exit                Exit
"""


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: Provider | None = None,
) -> int:
    """解析参数并运行单次任务或 REPL。"""
    raw = list(sys.argv[1:] if argv is None else argv)
    force_exec = bool(raw and raw[0] == "exec")
    if force_exec:
        raw = raw[1:]
    args = _parser().parse_args(raw)
    task = getattr(args, "task", None)
    one_shot = force_exec or task is not None
    if force_exec and not task:
        print("cade-mini: exec requires a task", file=sys.stderr)
        return 2
    if not one_shot and args.json:
        print("cade-mini: --json requires a one-shot task", file=sys.stderr)
        return 2

    project_root = args.project_root.resolve()
    if not project_root.is_dir():
        print(
            f"cade-mini: project root is not a directory: {project_root}",
            file=sys.stderr,
        )
        return 2

    sessions_dir = (
        args.sessions_dir.resolve()
        if args.sessions_dir is not None
        else project_root / ".cade-mini" / "sessions"
    )
    if args.list_sessions:
        _print_sessions(sessions_dir)
        return 0
    if args.continue_ and args.resume is not None:
        print(
            "cade-mini: --continue and --resume cannot be combined",
            file=sys.stderr,
        )
        return 2
    resume = "" if args.continue_ else args.resume

    try:
        selected = provider or build_provider(
            ProviderConfig(
                provider=args.provider,
                model=args.model,
                reasoning_effort=args.effort,
            )
        )
        budget = Budget(
            context_window=args.context_window
            or (getattr(selected, "context_window", None) or 200_000)
        )
    except (RuntimeError, ValueError) as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2

    tools = build_tools(project_root)
    policy = WorkspacePolicy(project_root, read_only=args.read_only)
    approve = _approver(args.yes)
    compactor = Compactor(selected)
    on_event: Callable[[LoopEvent], None] = _json_event if args.json else _human_event

    def context() -> tuple[str, ...]:
        if args.no_instructions:
            return ()
        text = load_project_instructions(project_root)
        return (text,) if text else ()

    try:
        log = _open_log(sessions_dir, resume)
    except (OSError, ValueError, LogError) as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2

    if one_shot:
        result = _run_task(
            log,
            task or "",
            selected,
            tools,
            policy,
            approve,
            budget,
            context(),
            args.max_steps,
            on_event,
            compactor,
        )
        if args.json:
            _final_json(result)
        else:
            print()
            if result.error:
                print(f"[{result.termination}] {result.error}", file=sys.stderr)
        return 0 if result.termination == "completed" else 1

    return _repl(
        log,
        sessions_dir,
        selected,
        tools,
        policy,
        approve,
        budget,
        context,
        args.max_steps,
        on_event,
        compactor,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cade-mini",
        description="A small coding agent with a durable session log.",
    )
    parser.add_argument("task", nargs="?", help="Task to run once; omit for REPL")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--sessions-dir", type=Path, default=None)
    parser.add_argument("--provider", default=ProviderConfig().provider)
    parser.add_argument("--model", default=ProviderConfig().model)
    parser.add_argument("--effort", default=ProviderConfig().reasoning_effort)
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        help="Resume a session by ID; omit the ID for the latest session",
    )
    parser.add_argument(
        "--continue",
        dest="continue_",
        action="store_true",
        help="Resume the latest session",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List sessions and exit",
    )
    parser.add_argument(
        "--no-instructions",
        action="store_true",
        help="Do not load AGENTS.md or CLAUDE.md",
    )
    parser.add_argument("--yes", action="store_true", help="Approve writes and shell")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Deny writes and shell commands",
    )
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--context-window", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Emit JSONL events")
    return parser


def _print_sessions(sessions_dir: Path) -> None:
    infos = list_sessions(sessions_dir)
    if not infos:
        print("No sessions.")
        return
    print(f"{'ID':<12} {'UPDATED':<25} TITLE")
    for info in infos:
        print(f"{info.id:<12} {info.updated_at:<25} {info.title}")


def _open_log(sessions_dir: Path, resume: str | None) -> SessionLog:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if resume is None:
        return _new_log(sessions_dir)
    if resume == "":
        paths = sorted(
            sessions_dir.glob("session-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not paths:
            return _new_log(sessions_dir)
        log = SessionLog.open(paths[0])
    else:
        log = SessionLog.open(sessions_dir / f"session-{resume}.jsonl")
    _ensure_system(log)
    return log


def _new_log(sessions_dir: Path) -> SessionLog:
    log = SessionLog.create(sessions_dir)
    log.append("system", {"text": SYSTEM_PROMPT})
    return log


def _ensure_system(log: SessionLog) -> None:
    if not any(entry.type == "system" for entry in log.branch()):
        log.append("system", {"text": SYSTEM_PROMPT})


def _run_task(
    log: SessionLog,
    task: str,
    provider: Provider,
    tools: Sequence[Tool],
    policy: WorkspacePolicy,
    approve: Callable[[ToolCallBlock], bool],
    budget: Budget,
    context: Sequence[str],
    max_steps: int,
    on_event: Callable[[LoopEvent], None],
    compactor: Compactor,
) -> RunResult:
    return asyncio.run(
        run(
            log,
            task,
            provider=provider,
            tools=tools,
            budget=budget,
            policy=policy,
            approve=approve,
            context=context,
            max_steps=max_steps,
            on_event=on_event,
            compact=compactor,
        )
    )


def _repl(
    log: SessionLog,
    sessions_dir: Path,
    provider: Provider,
    tools: Sequence[Tool],
    policy: WorkspacePolicy,
    approve: Callable[[ToolCallBlock], bool],
    budget: Budget,
    context_provider: Callable[[], tuple[str, ...]],
    max_steps: int,
    on_event: Callable[[LoopEvent], None],
    compactor: Compactor,
) -> int:
    print(f"Cade Mini session {log.session_id}. Enter a task, /help, or /exit.")
    while True:
        try:
            line = input("cade-mini> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return 0
        if text == "/help":
            print(_HELP)
            continue
        if text == "/context":
            conversation = project(log.branch())
            print(
                f"session={log.session_id} provider={provider.model} "
                f"budget={budget.input_budget} "
                f"messages={len(conversation)} events={len(log.branch())}"
            )
            continue
        if text == "/new":
            log = _new_log(sessions_dir)
            print(f"session {log.session_id}")
            continue
        if text.startswith("/resume"):
            target = text[len("/resume") :].strip()
            try:
                log = _open_log(sessions_dir, target)
            except (OSError, ValueError, LogError) as exc:
                print(f"cade-mini: {exc}", file=sys.stderr)
                continue
            print(f"session {log.session_id}")
            continue
        if text == "/compact":
            compacted = asyncio.run(compactor(log, budget))
            print("compacted" if compacted else "nothing to compact")
            continue
        if text.startswith("/new-context"):
            handoff = text[len("/new-context") :].strip() or None
            started = reset_window(log, handoff=handoff, reason="manual")
            print("window started" if started else "no task to reset")
            continue

        try:
            result = _run_task(
                log,
                text,
                provider,
                tools,
                policy,
                approve,
                budget,
                context_provider(),
                max_steps,
                on_event,
                compactor,
            )
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue
        print()
        if result.error:
            print(f"[{result.termination}] {result.error}", file=sys.stderr)


def _approver(auto_approve: bool) -> Callable[[ToolCallBlock], bool]:
    def approve(call: ToolCallBlock) -> bool:
        if auto_approve:
            return True
        if not sys.stdin.isatty():
            return False
        detail = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
        print(
            f"\nApprove {call.name} {detail}? [y/N] ",
            file=sys.stderr,
            end="",
            flush=True,
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}

    return approve


def _human_event(event: LoopEvent) -> None:
    if isinstance(event, TextDeltaEvent):
        print(event.text, end="", flush=True)
    elif isinstance(event, ToolStartEvent):
        print(f"\n[ tool {event.name} started ]", file=sys.stderr)
    else:
        status = "failed" if event.is_error else "done"
        print(f"[ tool {event.call_id} {status} ]", file=sys.stderr)


def _json_event(event: LoopEvent) -> None:
    if isinstance(event, TextDeltaEvent):
        payload: dict[str, Any] = {"type": "text", "text": event.text}
    elif isinstance(event, ToolStartEvent):
        payload = {
            "type": "tool_start",
            "call_id": event.call_id,
            "name": event.name,
        }
    else:
        payload = {
            "type": "tool_end",
            "call_id": event.call_id,
            "is_error": event.is_error,
        }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _final_json(result: RunResult) -> None:
    print(
        json.dumps(
            {
                "type": "final",
                "termination": result.termination,
                "answer": result.answer,
                "error": result.error,
                "usage": result.usage,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


__all__ = ["SYSTEM_PROMPT", "main"]
