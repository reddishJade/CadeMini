"""命令行参数、输出和退出码；应用装配与运行由 app 层负责。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from cade.app.bootstrap import create_session, resolve_sessions_dir, undo_session
from cade.app.config import CadeConfig
from cade.coding.goal import GoalStore
from cade.harness.events import (
    LoopEvent,
    RunResult,
    TextDeltaEvent,
    ToolEndEvent,
    ToolStartEvent,
)
from cade.harness.provider import Provider
from cade.harness.session.log import list_sessions
from cade.harness.session.types import LogError


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: Provider | None = None,
) -> int:
    """解析参数并在退出时清理会话临时目录。"""
    with ExitStack() as resources:
        return _main(argv, provider=provider, resources=resources)


def _main(
    argv: Sequence[str] | None,
    *,
    provider: Provider | None,
    resources: ExitStack,
) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "login":
        from cade.hosts.auth import login

        return login(raw[1:])
    force_exec = bool(raw and raw[0] == "exec")
    if force_exec:
        raw = raw[1:]
    args = _parser().parse_args(raw)
    task = args.task
    one_shot = force_exec or task is not None
    if force_exec and not task:
        print("cade: exec requires a task", file=sys.stderr)
        return 2
    if not one_shot and args.json:
        print("cade: --json requires a one-shot task", file=sys.stderr)
        return 2
    if (
        not one_shot
        and not args.list_sessions
        and not args.undo
        and (not sys.stdin.isatty() or not sys.stdout.isatty())
    ):
        print(
            "cade: the interactive TUI requires a terminal; "
            "provide a task or use 'cade exec TASK'",
            file=sys.stderr,
        )
        return 2
    project_root = args.project_root.resolve()
    if not project_root.is_dir():
        print(f"cade: project root is not a directory: {project_root}", file=sys.stderr)
        return 2
    try:
        config = CadeConfig.load(project_root, args.config)
    except ValueError as exc:
        print(f"cade: {exc}", file=sys.stderr)
        return 2
    overrides = {
        name: getattr(args, name)
        for name in (
            "provider",
            "model",
            "max_steps",
            "read_only",
            "instructions",
            "note",
            "memory",
            "auto_review",
            "mode",
            "context_window",
            "sandbox",
            "network",
        )
        if getattr(args, name) is not None
    }
    if args.effort is not None:
        overrides["reasoning_effort"] = args.effort
    if args.sessions_dir is not None:
        overrides["sessions_dir"] = str(args.sessions_dir)
    config = config.model_copy(update=overrides)
    if args.goal:
        GoalStore(project_root).set(args.goal)
    if not one_shot and args.steer:
        print("cade: --steer requires a task", file=sys.stderr)
        return 2
    if args.list_sessions:
        _print_sessions(resolve_sessions_dir(project_root, config))
        return 0
    if args.continue_ and args.resume is not None:
        print("cade: --continue and --resume cannot be combined", file=sys.stderr)
        return 2
    if args.undo:
        if one_shot:
            print("cade: --undo cannot be combined with a task", file=sys.stderr)
            return 2
        try:
            message = undo_session(project_root, config, args.resume)
        except (OSError, ValueError) as exc:
            print(f"cade: {exc}", file=sys.stderr)
            return 1
        print(message)
        return 1 if message == "Nothing to undo." else 0
    try:
        assembled = create_session(
            project_root,
            config,
            resources=resources,
            provider=provider,
            interactive=not one_shot,
            resume="" if args.continue_ else args.resume,
            auto_approve=args.yes,
            config_path=args.config,
        )
    except (OSError, ValueError, RuntimeError, LogError) as exc:
        print(f"cade: {exc}", file=sys.stderr)
        return 2
    service = assembled.session
    service.steering.extend(args.steer or ())
    for warning in assembled.warnings:
        print(f"cade: mcp: {warning}", file=sys.stderr)
    if one_shot:
        try:
            result = asyncio.run(
                service.run_task(
                    task or "",
                    _json_event if args.json else _human_event,
                    approver=assembled.approver,
                )
            )
        except (LogError, OSError, ValueError) as exc:
            result = RunResult(
                answer="", steps=0, termination="aborted", error=str(exc)
            )
        if args.json:
            _final_json(result)
        else:
            print()
            if result.error:
                print(f"[{result.termination}] {result.error}", file=sys.stderr)
        return 0 if result.termination == "completed" else 1

    from cade.hosts.tui.app import run_tui

    return run_tui(
        service,
        project_root,
        session_id=service.log.session_id,
        config_path=args.config,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cade",
        description="A small coding agent with a durable session log.",
        epilog="Use 'cade login --help' for authentication options.",
    )
    parser.add_argument(
        "task", nargs="?", help="Task to run once; omit to start the TUI"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None, help="Config file")
    parser.add_argument("--goal", default=None, help="Set the workspace goal")
    parser.add_argument(
        "--steer",
        action="append",
        default=None,
        help="Inject guidance at the next model step of a one-shot task",
    )
    parser.add_argument("--sessions-dir", type=Path, default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None)
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
        "--undo",
        action="store_true",
        help="Undo one action in the latest or --resume ID session and exit",
    )
    parser.add_argument(
        "--instructions",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Load AGENTS.md or CLAUDE.md (default: on)",
    )
    parser.add_argument(
        "--note",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Load NOTE.md working state (default: on)",
    )
    parser.add_argument(
        "--memory",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Load MEMORY.md durable facts (default: on)",
    )
    parser.add_argument(
        "--auto-review",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Let the model review ask-level tool calls (default: off)",
    )
    parser.add_argument("--yes", action="store_true", help="Approve writes and shell")
    parser.add_argument(
        "--read-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Deny writes and permit only read-only shell commands",
    )
    parser.add_argument(
        "--mode",
        choices=("plan", "build", "ask"),
        default=None,
        help="Execution mode (default: build)",
    )
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default=None,
        help="Filesystem sandbox policy (default: workspace-write)",
    )
    parser.add_argument(
        "--network",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow tool network access (default: on; OS enforcement on Linux/macOS only)",
    )
    parser.add_argument("--max-steps", type=int, default=None)
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


def _human_event(event: LoopEvent) -> None:
    if isinstance(event, TextDeltaEvent):
        print(event.text, end="", flush=True)
    elif isinstance(event, ToolStartEvent):
        print(f"\n[ tool {event.name} started ]", file=sys.stderr)
    elif isinstance(event, ToolEndEvent):
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
    elif isinstance(event, ToolEndEvent):
        payload = {
            "type": "tool_end",
            "call_id": event.call_id,
            "is_error": event.is_error,
        }
    else:
        return
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


__all__ = ["main"]
