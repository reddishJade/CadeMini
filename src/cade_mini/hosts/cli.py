"""Cade Mini 的命令行入口：单次任务与交互式 TUI。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

from cade_mini.app.config import MiniConfig
from cade_mini.app.instructions import load_project_instructions
from cade_mini.app.note import load_working_note
from cade_mini.app.prompts import render_system_prompt
from cade_mini.app.provider import ProviderConfig, build_provider
from cade_mini.app.recovery import WorkspaceRecovery
from cade_mini.core.compaction import Compactor
from cade_mini.core.log import SessionLog, list_sessions
from cade_mini.core.loop import (
    LoopEvent,
    Provider,
    RunResult,
    TextDeltaEvent,
    Tool,
    ToolEndEvent,
    ToolStartEvent,
    run,
)
from cade_mini.core.permissions import (
    ApprovalRequest,
    ApprovalResult,
    Approver,
)
from cade_mini.core.request import Budget
from cade_mini.core.types import LogError
from cade_mini.core.window import reset_window
from cade_mini.execution_env.sandbox import PlatformSandbox
from cade_mini.extensions.goal import GoalStore, GoalTool
from cade_mini.extensions.hooks import HookRunner
from cade_mini.extensions.mcp import discover_mcp_tools
from cade_mini.extensions.memory import MemoryTool, load_memory
from cade_mini.extensions.skills import (
    LoadSkillTool,
    discover_skills,
    render_skill_catalog,
)
from cade_mini.extensions.snapshots import SnapshotStore
from cade_mini.extensions.subagents import (
    DelegateTool,
    SubagentRunner,
    SubagentTool,
)
from cade_mini.safety.approval import SessionApprover
from cade_mini.safety.grants import GrantStore
from cade_mini.safety.policy import Mode, WorkspacePolicy, filter_tools
from cade_mini.safety.review import AutoReviewer
from cade_mini.tools.history import HistoryTool
from cade_mini.tools.workspace import build_tools

WINDOW_HANDOFF = (
    "Earlier history was discarded to fit the model window. "
    "Consult NOTE.md and use the history tool with an entry_id or call_id "
    "to retrieve exact details."
)


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
        from cade_mini.hosts.auth import login

        return login(raw[1:])
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
    if (
        not one_shot
        and not args.list_sessions
        and not args.undo
        and (not sys.stdin.isatty() or not sys.stdout.isatty())
    ):
        print(
            "cade-mini: the interactive TUI requires a terminal; "
            "provide a task or use 'cade-mini exec TASK'",
            file=sys.stderr,
        )
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
    steering = deque(args.steer or [])
    if not one_shot and steering:
        print("cade-mini: --steer requires a task", file=sys.stderr)
        return 2

    def next_steering() -> str | None:
        return steering.popleft() if steering else None

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
        try:
            snapshot = SnapshotStore(project_root).undo_last()
        except (OSError, ValueError) as exc:
            print(f"cade-mini: {exc}", file=sys.stderr)
            return 1
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
        context_window = args.context_window or config.context_window
        provider_config = ProviderConfig(
            provider=provider_name,
            model=model_name,
            reasoning_effort=effort,
            context_window=context_window,
        )
        try:
            selected = provider or build_provider(provider_config)
        except RuntimeError as exc:
            if one_shot:
                raise
            from cade_mini.hosts.interaction import UnavailableProvider

            selected = UnavailableProvider(model_name, provider_name, str(exc))
        resolved_window = context_window or getattr(selected, "context_window", None)
        if resolved_window is None:
            raise ValueError(
                "Model context window is unknown; register the model with "
                "metadata or pass --context-window"
            )
        budget = Budget(context_window=resolved_window)
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
    snapshots = SnapshotStore(project_root)
    temp_dir = Path(
        resources.enter_context(tempfile.TemporaryDirectory(prefix="cade-mini-"))
    )
    sandbox = PlatformSandbox(
        project_root,
        temp_dir,
        mode=args.sandbox or config.sandbox,
        network=args.network if args.network is not None else config.network,
        protected_paths=(
            project_root / ".git" / "hooks",
            project_root / ".git" / "config",
        ),
        hidden_paths=(project_root / ".cade-mini", Path.home() / ".cade-mini"),
    )
    core_tools = (
        *build_tools(
            project_root,
            snapshots=snapshots,
            sandbox=sandbox,
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
        sandbox=sandbox.mode,
        temp_dir=temp_dir,
        rules=config.rules,
        grants=grants,
    )
    approve: Approver = SessionApprover(auto_approve=args.yes, grants=grants)
    hooks = HookRunner(config.hooks, project_root)
    if use_auto_review and not args.yes:
        approve = AutoReviewer(selected, workspace=project_root)
    approval_router = _ApprovalRouter(approve)

    def note_context() -> str | None:
        return load_working_note(project_root) if use_note else None

    compactor = Compactor(selected, extra_context=note_context)
    on_event: Callable[[LoopEvent], None] = _json_event if args.json else _human_event

    def fallback_window(log: SessionLog) -> bool:
        return reset_window(log, handoff=WINDOW_HANDOFF, reason="fallback")

    def context() -> tuple[str, ...]:
        parts: list[str] = [policy.describe()]
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

    recovery = WorkspaceRecovery(
        snapshots, system_prompt=system_prompt, read_only=policy.read_only
    )
    subagent_runner = SubagentRunner(
        workspace=project_root,
        provider=selected,
        tools=core_tools,
        budget=budget,
        sessions_dir=sessions_dir,
        system_prompt=system_prompt,
        policy=policy,
        approve=approval_router,
        context=context,
        max_steps=min(max_steps, 20),
        hooks=hooks,
        lifecycle=recovery,
    )
    base_tools = (
        *core_tools,
        SubagentTool(runner=subagent_runner),
        DelegateTool(runner=subagent_runner),
    )

    try:
        log = _open_log(
            sessions_dir,
            resume,
            system_prompt,
            project_root,
            deferred=not one_shot,
            recovery=recovery,
        )
        recovery.recover(log)
    except (OSError, ValueError, LogError) as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2
    tools = _tools_for(base_tools, project_root, log)

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
            next_steering,
            recovery,
        )
        if args.json:
            _final_json(result)
        else:
            print()
            if result.error:
                print(f"[{result.termination}] {result.error}", file=sys.stderr)
        return 0 if result.termination == "completed" else 1

    from cade_mini.hosts.interaction import InteractionService
    from cade_mini.hosts.tui.app import run_tui

    service = InteractionService(
        log=log,
        sessions_dir=sessions_dir,
        system_prompt=system_prompt,
        provider=selected,
        provider_config=ProviderConfig(
            provider=provider_name,
            model=model_name,
            reasoning_effort=effort,
            context_window=context_window,
        ),
        tools=base_tools,
        skills=skills,
        workspace=project_root,
        snapshots=snapshots,
        goals=goals,
        policy=policy,
        budget=budget,
        context=context,
        max_steps=max_steps,
        compactor=compactor,
        hooks=hooks,
        window=fallback_window,
        automatic_approver=approve if args.yes or use_auto_review else None,
        config_path=args.config,
        context_window_explicit=bool(args.context_window or config.context_window),
    )
    service.auto_approve = bool(args.yes)
    return run_tui(
        service,
        project_root,
        session_id=log.session_id,
        config_path=args.config,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cade-mini",
        description="A small coding agent with a durable session log.",
        epilog="Use 'cade-mini login --help' for authentication options.",
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
        help="Restore the latest file snapshot and exit",
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


def _open_log(
    sessions_dir: Path,
    resume: str | None,
    system_prompt: str,
    project_root: Path,
    *,
    deferred: bool = False,
    recovery: WorkspaceRecovery | None = None,
) -> SessionLog:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if resume is None:
        return _new_log(sessions_dir, system_prompt, project_root, deferred=deferred)
    if resume == "":
        sessions = list_sessions(sessions_dir)
        if not sessions:
            return _new_log(
                sessions_dir, system_prompt, project_root, deferred=deferred
            )
        log = SessionLog.open(sessions[0].path)
    else:
        log = SessionLog.open(sessions_dir / f"session-{resume}.jsonl")
    if recovery is not None:
        recovery.recover(log)
    _ensure_system(log, system_prompt)
    return log


def _new_log(
    sessions_dir: Path,
    system_prompt: str,
    project_root: Path,
    *,
    deferred: bool = False,
) -> SessionLog:
    log = SessionLog.create(sessions_dir, project_root=project_root, deferred=deferred)
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
    approve: Approver,
    budget: Budget,
    context: Sequence[str],
    max_steps: int,
    on_event: Callable[[LoopEvent], None],
    compactor: Compactor,
    hooks: HookRunner | None,
    window: Callable[[SessionLog], bool] | None,
    steering: Callable[[], str | None] | None = None,
    lifecycle: WorkspaceRecovery | None = None,
) -> RunResult:
    tools = filter_tools(tools, policy.mode)
    try:
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
                lifecycle=lifecycle,
                max_steps=max_steps,
                on_event=on_event,
                compact=compactor,
                window=window,
                hooks=hooks,
                steering=steering,
            )
        )
    except (LogError, OSError, ValueError) as exc:
        return RunResult(answer="", steps=0, termination="aborted", error=str(exc))


class _ApprovalRouter:
    """让子代理在交互式 TUI 和 one-shot 模式之间复用同一个审批入口。"""

    def __init__(
        self,
        target: Approver,
    ) -> None:
        self.target = target

    def __call__(
        self, request: ApprovalRequest
    ) -> ApprovalResult | Awaitable[ApprovalResult]:
        return self.target(request)


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
