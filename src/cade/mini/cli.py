"""Cade Mini 的命令行入口：单次任务与交互式 REPL。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, cast

from .approval import SessionApprover
from .compaction import Compactor
from .config import MiniConfig
from .goal import GoalStore, GoalTool
from .grants import GrantStore
from .history import HistoryTool
from .hooks import HookRunner
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
from .mcp import discover_mcp_tools
from .memory import MemoryTool, load_memory
from .note import load_working_note
from .policy import Mode, WorkspacePolicy, filter_tools
from .projection import project
from .prompts import render_system_prompt
from .provider import ProviderConfig, build_provider
from .request import Budget
from .review import AutoReviewer
from .sandbox import MiniSandboxMode, build_sandbox
from .skills import LoadSkillTool, discover_skills, render_skill_catalog
from .snapshots import SnapshotStore
from .subagents import SubagentTool
from .tools import build_tools
from .types import LogError, ToolCallBlock
from .web import WebHost
from .window import reset_window

WINDOW_HANDOFF = (
    "Earlier history was discarded to fit the model window. "
    "Consult NOTE.md and use the history tool with an entry_id or call_id "
    "to retrieve exact details."
)

_HELP = """\
Commands:
  /new                 Start a new session
  /resume [ID]         Resume a session (default: latest)
  /compact             Summarize older turns and keep recent work
  /new-context [NOTE]  Start a new window, optionally with a handoff note
  /mode [MODE]         Show or switch plan/build/act
  /permissions [clear] Show or clear session grants
  /undo                Restore the latest file snapshot
  /goal [TEXT|done]    Show, set, complete, or clear the goal
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
    try:
        config = MiniConfig.load(project_root, args.config)
    except ValueError as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2
    goals = GoalStore(project_root)
    if args.goal:
        goals.set(args.goal)

    provider_name = args.provider or config.provider or ProviderConfig().provider
    model_name = args.model or config.model or ProviderConfig().model
    if args.effort is not None:
        effort = args.effort
    elif config.reasoning_effort is not None:
        effort = config.reasoning_effort
    else:
        effort = ProviderConfig().reasoning_effort
    max_steps = args.max_steps or config.max_steps or 50
    read_only = args.read_only if args.read_only is not None else config.read_only
    use_instructions = (
        args.instructions if args.instructions is not None else config.instructions
    )
    use_note = args.note if args.note is not None else config.note
    use_memory = args.memory if args.memory is not None else config.memory
    use_auto_review = (
        args.auto_review if args.auto_review is not None else config.auto_review
    )
    mode = cast(Mode, args.mode or config.mode)
    sandbox_mode = cast(
        MiniSandboxMode,
        args.sandbox or config.sandbox,
    )

    raw_sessions = args.sessions_dir or (
        Path(config.sessions_dir) if config.sessions_dir else None
    )
    if raw_sessions is None:
        sessions_dir = project_root / ".cade-mini" / "sessions"
    elif raw_sessions.is_absolute():
        sessions_dir = raw_sessions
    else:
        sessions_dir = project_root / raw_sessions
    sessions_dir = sessions_dir.resolve()

    if args.list_sessions:
        _print_sessions(sessions_dir)
        return 0
    if args.undo:
        snapshot = SnapshotStore(project_root).undo_last()
        if snapshot is None:
            print("cade-mini: no snapshots to undo", file=sys.stderr)
            return 1
        print(f"restored {snapshot.path}")
        return 0
    if args.continue_ and args.resume is not None:
        print(
            "cade-mini: --continue and --resume cannot be combined",
            file=sys.stderr,
        )
        return 2
    resume = "" if args.continue_ else args.resume
    try:
        system_prompt = render_system_prompt(project_root, config.prompt_modules)
    except ValueError as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2

    try:
        selected = provider or build_provider(
            ProviderConfig(
                provider=provider_name,
                model=model_name,
                reasoning_effort=effort,
            )
        )
        budget = Budget(
            context_window=args.context_window
            or config.context_window
            or (getattr(selected, "context_window", None) or 200_000)
        )
    except (RuntimeError, ValueError) as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2

    skills = discover_skills(project_root)
    skill_catalog = render_skill_catalog(skills)
    mcp_tools, mcp_errors = asyncio.run(
        discover_mcp_tools(config.mcp_servers, project_root)
    )
    for error in mcp_errors:
        print(f"cade-mini: mcp: {error}", file=sys.stderr)
    sandbox = build_sandbox(sandbox_mode, project_root)
    snapshots = SnapshotStore(project_root)
    core_tools = (
        *build_tools(
            project_root,
            sandbox=sandbox,
            snapshots=snapshots,
        ),
        LoadSkillTool(project_root, skills),
        MemoryTool(project_root),
        GoalTool(project_root),
        *mcp_tools,
    )
    grants = GrantStore()
    policy = WorkspacePolicy(
        project_root,
        read_only=read_only,
        mode=mode,
        rules=config.rules,
        grants=grants,
    )
    approve = SessionApprover(auto_approve=args.yes, grants=grants)
    hooks = HookRunner(config.hooks, project_root)
    if use_auto_review and not args.yes:
        approve = AutoReviewer(selected, workspace=project_root)

    def note_context() -> str | None:
        return load_working_note(project_root) if use_note else None

    compactor = Compactor(selected, extra_context=note_context)
    on_event: Callable[[LoopEvent], None] = _json_event if args.json else _human_event

    def fallback_window(log: SessionLog) -> bool:
        return reset_window(log, handoff=WINDOW_HANDOFF, reason="fallback")

    def context() -> tuple[str, ...]:
        parts: list[str] = []
        if use_instructions:
            text = load_project_instructions(project_root)
            if text:
                parts.append(text)
        if skill_catalog:
            parts.append(skill_catalog)
        note = note_context()
        if note:
            parts.append(note)
        memory = load_memory(project_root) if use_memory else None
        if memory:
            parts.append(memory)
        goal = goals.load()
        if goal is not None and goal.status == "active":
            parts.append(f"<goal>\n{goal.objective}\n</goal>")
        return tuple(parts)

    subagent = SubagentTool(
        workspace=project_root,
        provider=selected,
        tools=core_tools,
        budget=budget,
        sessions_dir=sessions_dir,
        system_prompt=system_prompt,
        policy=policy,
        approve=approve,
        context=context,
        max_steps=min(max_steps, 20),
        hooks=hooks,
    )
    base_tools = (*core_tools, subagent)

    try:
        log = _open_log(sessions_dir, resume, system_prompt)
    except (OSError, ValueError, LogError) as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2
    tools = _tools_for(base_tools, project_root, log)

    if args.web:
        web_approve = SessionApprover(
            auto_approve=args.yes,
            is_tty=lambda: False,
        )

        def web_runner(task: str) -> RunResult:
            return _run_task(
                log,
                task,
                selected,
                tools,
                policy,
                web_approve,
                budget,
                context(),
                max_steps,
                lambda event: None,
                compactor,
                hooks,
                fallback_window,
            )

        WebHost(
            runner=web_runner,
            log=log,
            sessions_dir=sessions_dir,
            host=args.host,
            port=args.port,
        ).serve_forever()
        return 0

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
            max_steps,
            on_event,
            compactor,
            hooks,
            fallback_window,
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
        system_prompt,
        selected,
        base_tools,
        project_root,
        snapshots,
        goals,
        policy,
        approve,
        budget,
        context,
        max_steps,
        on_event,
        compactor,
        hooks,
        fallback_window,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cade-mini",
        description="A small coding agent with a durable session log.",
    )
    parser.add_argument("task", nargs="?", help="Task to run once; omit for REPL")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None, help="Config file")
    parser.add_argument("--goal", default=None, help="Set the workspace goal")
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
        help="Restore the latest file snapshot and exit",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Start the local web host instead of the REPL",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
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
        help="Deny writes and shell commands",
    )
    parser.add_argument(
        "--mode",
        choices=("plan", "build", "act"),
        default=None,
        help="Execution mode (default: act)",
    )
    parser.add_argument(
        "--sandbox",
        choices=("off", "workspace", "strict"),
        default=None,
        help="OS sandbox mode on Linux (default: workspace)",
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


def _open_log(
    sessions_dir: Path,
    resume: str | None,
    system_prompt: str,
) -> SessionLog:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if resume is None:
        return _new_log(sessions_dir, system_prompt)
    if resume == "":
        paths = sorted(
            sessions_dir.glob("session-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not paths:
            return _new_log(sessions_dir, system_prompt)
        log = SessionLog.open(paths[0])
    else:
        log = SessionLog.open(sessions_dir / f"session-{resume}.jsonl")
    _ensure_system(log, system_prompt)
    return log


def _new_log(sessions_dir: Path, system_prompt: str) -> SessionLog:
    log = SessionLog.create(sessions_dir)
    log.append("system", {"text": system_prompt})
    return log


def _ensure_system(log: SessionLog, system_prompt: str) -> None:
    if not any(entry.type == "system" for entry in log.branch()):
        log.append("system", {"text": system_prompt})


def _tools_for(
    base_tools: Sequence[Tool],
    workspace: Path,
    log: SessionLog,
) -> tuple[Tool, ...]:
    return (*base_tools, HistoryTool(workspace, log))


def _run_task(
    log: SessionLog,
    task: str,
    provider: Provider,
    tools: Sequence[Tool],
    policy: WorkspacePolicy,
    approve: Callable[[ToolCallBlock], bool | Awaitable[bool]],
    budget: Budget,
    context: Sequence[str],
    max_steps: int,
    on_event: Callable[[LoopEvent], None],
    compactor: Compactor,
    hooks: HookRunner | None,
    window: Callable[[SessionLog], bool] | None,
) -> RunResult:
    tools = filter_tools(tools, policy.mode)
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
            window=window,
            hooks=hooks,
        )
    )


def _repl(
    log: SessionLog,
    sessions_dir: Path,
    system_prompt: str,
    provider: Provider,
    base_tools: Sequence[Tool],
    workspace: Path,
    snapshots: SnapshotStore,
    goals: GoalStore,
    policy: WorkspacePolicy,
    approve: Callable[[ToolCallBlock], bool | Awaitable[bool]],
    budget: Budget,
    context_provider: Callable[[], tuple[str, ...]],
    max_steps: int,
    on_event: Callable[[LoopEvent], None],
    compactor: Compactor,
    hooks: HookRunner | None,
    window: Callable[[SessionLog], bool] | None,
) -> int:
    tools = _tools_for(base_tools, workspace, log)
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
            log = _new_log(sessions_dir, system_prompt)
            tools = _tools_for(base_tools, workspace, log)
            print(f"session {log.session_id}")
            continue
        if text.startswith("/resume"):
            target = text[len("/resume") :].strip()
            try:
                log = _open_log(sessions_dir, target, system_prompt)
            except (OSError, ValueError, LogError) as exc:
                print(f"cade-mini: {exc}", file=sys.stderr)
                continue
            tools = _tools_for(base_tools, workspace, log)
            print(f"session {log.session_id}")
            continue
        if text.startswith("/mode"):
            value = text[len("/mode") :].strip()
            if not value:
                print(f"mode={policy.mode}")
                continue
            try:
                policy.set_mode(value)
            except ValueError as exc:
                print(f"cade-mini: {exc}", file=sys.stderr)
                continue
            print(f"mode={policy.mode}")
            continue
        if text.startswith("/permissions"):
            value = text[len("/permissions") :].strip()
            if value == "clear":
                policy.grants.clear()
                print("permissions cleared")
            elif value:
                print("usage: /permissions [clear]", file=sys.stderr)
            else:
                grants = policy.grants.describe()
                print("\n".join(grants) if grants else "no session grants")
            continue
        if text == "/undo":
            snapshot = snapshots.undo_last()
            if snapshot is None:
                print("no snapshots to undo")
            else:
                print(f"restored {snapshot.path}")
            continue
        if text.startswith("/goal"):
            value = text[len("/goal") :].strip()
            if not value:
                goal = goals.load()
                print("no goal" if goal is None else f"{goal.status}: {goal.objective}")
            elif value == "done":
                goal = goals.complete()
                print("goal completed" if goal is not None else "no goal")
            elif value == "clear":
                goals.clear()
                print("goal cleared")
            else:
                goal = goals.set(value)
                print(f"goal set: {goal.objective}")
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
                hooks,
                window,
            )
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue
        print()
        if result.error:
            print(f"[{result.termination}] {result.error}", file=sys.stderr)


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


__all__ = ["main"]
