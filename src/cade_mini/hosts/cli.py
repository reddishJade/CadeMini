"""Cade Mini 的命令行入口：单次任务与交互式 REPL。"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import queue
import re
import sys
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cade_mini.app.config import MiniConfig
from cade_mini.app.instructions import load_project_instructions
from cade_mini.app.note import load_working_note
from cade_mini.app.prompts import render_system_prompt
from cade_mini.app.provider import ProviderConfig, build_provider
from cade_mini.core.compaction import Compactor
from cade_mini.core.log import SessionLog, list_sessions
from cade_mini.core.loop import (
    CancellationToken,
    LoopEvent,
    Provider,
    ReasoningDeltaEvent,
    RunResult,
    TextDeltaEvent,
    Tool,
    ToolEndEvent,
    ToolStartEvent,
    run,
)
from cade_mini.core.projection import project
from cade_mini.core.request import Budget
from cade_mini.core.types import LogEntry, LogError, ToolCallBlock
from cade_mini.core.window import reset_window
from cade_mini.extensions.goal import GoalStore, GoalTool
from cade_mini.extensions.hooks import HookRunner
from cade_mini.extensions.mcp import discover_mcp_tools
from cade_mini.extensions.memory import MemoryTool, load_memory
from cade_mini.extensions.skills import (
    LoadSkillTool,
    Skill,
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
from cade_mini.tools.workspace import build_tools, resolve_workspace_path

WINDOW_HANDOFF = (
    "Earlier history was discarded to fit the model window. "
    "Consult NOTE.md and use the history tool with an entry_id or call_id "
    "to retrieve exact details."
)

_HELP = """\
Commands:
  /new                 Start a new session
  /resume [ID]         Resume a session (default: latest)
  /sessions            List project sessions
  /fork [N|ENTRY]      Fork before a recent user turn or entry; N=1 is latest
  /clone               Copy the current branch to a new session
  /tree                Show session entries; * marks the active branch
  /rewind [N]          Create a new session without the latest N user turns
  /compact             Summarize older turns and keep recent work
  /new-context [NOTE]  Start a new window, optionally with a handoff note
  /mode [MODE]         Show or switch plan/build/act
  /permissions [clear] Show or clear session grants
  /undo [N]            Restore the latest N file snapshots
  /goal [TEXT|done]    Show, set, complete, or clear the goal
  /context             Show session and budget information
  /steer TEXT          Queue guidance for the next model step
  /queue TEXT          Queue a task to run after the current one
  /stop                Cancel the current task
  /help                Show this help
  /exit                Exit

Input:
  @path                Attach a workspace file to the task
  $skill               Load a skill body into the task
  !command             Run a shell command without contacting the model
"""


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: Provider | None = None,
) -> int:
    """解析参数并运行单次任务或 REPL。"""
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

    project_root = args.project_root.resolve()
    if not project_root.is_dir():
        print(
            f"cade-mini: project root is not a directory: {project_root}",
            file=sys.stderr,
        )
        return 2
    if (
        provider is None
        and not one_shot
        and not args.list_sessions
        and not args.undo
        and args.tui is not False
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and args.config is None
        and not (args.provider or args.model)
        and not (project_root / "cade-mini.json").is_file()
    ):
        from cade_mini.hosts.tui.setup import run_first_time_setup

        try:
            if not run_first_time_setup(project_root):
                print("cade-mini: setup cancelled", file=sys.stderr)
                return 130
        except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as exc:
            print(f"cade-mini: setup failed: {exc}", file=sys.stderr)
            return 1

    try:
        config = MiniConfig.load(project_root, args.config)
    except ValueError as exc:
        print(f"cade-mini: {exc}", file=sys.stderr)
        return 2
    goals = GoalStore(project_root)
    if args.goal:
        goals.set(args.goal)
    steering = deque(args.steer or [])
    pending = deque(args.queue or [])
    if one_shot and pending:
        print("cade-mini: --queue requires the REPL", file=sys.stderr)
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
        if provider is not None:
            selected = provider
        else:
            requested = ProviderConfig(
                provider=provider_name,
                model=model_name,
                reasoning_effort=effort,
            )
            try:
                selected = build_provider(requested)
            except RuntimeError as exc:
                interactive = not one_shot and (
                    args.tui is True
                    or (args.tui is None and sys.stdin.isatty() and sys.stdout.isatty())
                )
                if not interactive:
                    raise
                from cade_mini.hosts.interaction import UnavailableProvider

                selected = UnavailableProvider(
                    model=model_name,
                    transport=provider_name,
                    reason=str(exc),
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
    snapshots = SnapshotStore(project_root)
    core_tools = (
        *build_tools(
            project_root,
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
    approval_router = _ApprovalRouter(approve)

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
    )
    base_tools = (
        *core_tools,
        SubagentTool(runner=subagent_runner),
        DelegateTool(runner=subagent_runner),
    )

    try:
        log = _open_log(sessions_dir, resume, system_prompt)
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
        )
        if args.json:
            _final_json(result)
        else:
            print()
            if result.error:
                print(f"[{result.termination}] {result.error}", file=sys.stderr)
        return 0 if result.termination == "completed" else 1

    if args.tui is True or (
        args.tui is None and sys.stdin.isatty() and sys.stdout.isatty()
    ):
        from cade_mini.hosts.interaction import InteractionService
        from cade_mini.hosts.tui.app import run_tui

        return run_tui(
            InteractionService(
                log=log,
                sessions_dir=sessions_dir,
                system_prompt=system_prompt,
                provider=selected,
                provider_config=ProviderConfig(
                    provider=provider_name,
                    model=model_name,
                    reasoning_effort=effort,
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
                approval_router=approval_router,
                automatic_approver=approve if args.yes or use_auto_review else None,
                config_path=args.config,
                context_window_explicit=(
                    args.context_window is not None or config.context_window is not None
                ),
            )
        )

    return _repl(
        log,
        sessions_dir,
        system_prompt,
        selected,
        base_tools,
        skills,
        project_root,
        snapshots,
        goals,
        policy,
        approve,
        approval_router,
        budget,
        context,
        max_steps,
        on_event,
        compactor,
        hooks,
        fallback_window,
        steering,
        pending,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cade-mini",
        description="A small coding agent with a durable session log.",
        epilog="Use 'cade-mini login --help' for authentication options.",
    )
    parser.add_argument("task", nargs="?", help="Task to run once; omit for REPL")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None, help="Config file")
    parser.add_argument("--goal", default=None, help="Set the workspace goal")
    parser.add_argument(
        "--steer",
        action="append",
        default=None,
        help="Inject guidance at the next model step",
    )
    parser.add_argument(
        "--queue",
        action="append",
        default=None,
        help="Queue a task after the current task",
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
        help="Deny writes and shell commands",
    )
    parser.add_argument(
        "--mode",
        choices=("plan", "build", "act"),
        default=None,
        help="Execution mode (default: act)",
    )
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--context-window", type=int, default=None)
    parser.add_argument(
        "--tui",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use the interactive terminal workspace (default for terminals)",
    )
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
    steering: Callable[[], str | None] | None = None,
    cancel: CancellationToken | None = None,
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
                max_steps=max_steps,
                on_event=on_event,
                compact=compactor,
                window=window,
                hooks=hooks,
                steering=steering,
                cancel=cancel,
            )
        )
    except LogError as exc:
        return RunResult(answer="", steps=0, termination="aborted", error=str(exc))


@dataclass(frozen=True)
class _Line:
    text: str
    eof: bool = False


@dataclass
class _ApprovalRequest:
    call: ToolCallBlock
    answer: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class _TaskDone:
    result: RunResult


class _InputPump(threading.Thread):
    """把 stdin 行转成事件，让主线程可以在任务运行时继续处理输入。"""

    def __init__(self, events: queue.Queue[object]) -> None:
        super().__init__(name="cade-mini-input", daemon=True)
        self.events = events

    def run(self) -> None:
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt, OSError):
                self.events.put(_Line("", eof=True))
                return
            self.events.put(_Line(line))


class _ApprovalRouter:
    """让子代理在 REPL 和 one-shot 模式之间复用同一个审批入口。"""

    def __init__(
        self,
        target: Callable[[ToolCallBlock], bool | Awaitable[bool]],
    ) -> None:
        self.target = target

    def __call__(self, call: ToolCallBlock) -> bool | Awaitable[bool]:
        return self.target(call)


class _HostApprover:
    """任务线程通过事件队列请求主线程完成审批。"""

    def __init__(
        self,
        events: queue.Queue[object],
        grants: GrantStore,
        *,
        auto_approve: bool = False,
    ) -> None:
        self.events = events
        self.grants = grants
        self.auto_approve = auto_approve

    def __call__(self, call: ToolCallBlock) -> bool:
        if self.auto_approve or self.grants.match(call):
            return True
        request = _ApprovalRequest(call)
        self.events.put(request)
        request.done.wait()
        answer = (request.answer or "").strip().lower()
        if answer in {"a", "always"}:
            self.grants.add(call)
        return answer in {"y", "yes", "a", "always"}


class _ConsoleApprover:
    """主线程执行 ! 命令时直接读取审批答案。"""

    def __init__(
        self,
        events: queue.Queue[object],
        grants: GrantStore,
        *,
        auto_approve: bool = False,
    ) -> None:
        self.events = events
        self.grants = grants
        self.auto_approve = auto_approve

    def __call__(self, call: ToolCallBlock) -> bool:
        if self.auto_approve or self.grants.match(call):
            return True
        request = _ApprovalRequest(call)
        _answer_approval(self.events, request)
        answer = (request.answer or "").strip().lower()
        if answer in {"a", "always"}:
            self.grants.add(call)
        return answer in {"y", "yes", "a", "always"}


def _answer_approval(
    events: queue.Queue[object],
    request: _ApprovalRequest,
) -> None:
    detail = json.dumps(
        request.call.args,
        ensure_ascii=False,
        sort_keys=True,
    )
    if len(detail) > 500:
        detail = detail[:500] + "..."
    print(
        f"\nApprove {request.call.name} {detail}? [y]es/[n]o/[a]lways: ",
        file=sys.stderr,
        end="",
        flush=True,
    )
    event = events.get()
    answer = event.text if isinstance(event, _Line) else ""
    request.answer = answer
    request.done.set()


def _handle_live_input(
    text: str,
    pending: deque[str],
    steering: deque[str],
    cancel: CancellationToken,
) -> bool:
    """处理任务运行中的输入；返回是否请求退出。"""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("/steer"):
        value = stripped[len("/steer") :].strip()
        if value:
            steering.append(value)
            print("guidance queued", file=sys.stderr)
        return False
    if stripped.startswith("/queue"):
        value = stripped[len("/queue") :].strip()
        if value:
            pending.append(value)
            print("task queued", file=sys.stderr)
        return False
    if stripped in {"/stop", "/cancel"}:
        cancel.cancel("user stopped the task")
        print("stopping the current task", file=sys.stderr)
        return False
    if stripped in {"/exit", "/quit"}:
        cancel.cancel("user exited")
        return True
    pending.append(stripped)
    print("task queued", file=sys.stderr)
    return False


def _run_task_live(
    events: queue.Queue[object],
    pending: deque[str],
    steering: deque[str],
    cancel: CancellationToken,
    worker: threading.Thread,
) -> tuple[RunResult, bool]:
    worker.start()
    exit_requested = False
    while True:
        event = events.get()
        if isinstance(event, _TaskDone):
            return event.result, exit_requested
        if isinstance(event, _ApprovalRequest):
            _answer_approval(events, event)
            continue
        if isinstance(event, _Line):
            if event.eof:
                exit_requested = True
                cancel.cancel("input closed")
            else:
                exit_requested = (
                    _handle_live_input(event.text, pending, steering, cancel)
                    or exit_requested
                )
                if not exit_requested:
                    print("cade-mini> ", end="", flush=True)


def _entry_preview(entry: LogEntry) -> str:
    data = entry.data
    for key in ("text", "content", "summary", "handoff"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text[:80]
    if entry.type == "assistant":
        blocks = data.get("blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_text = block.get("text")
                if isinstance(block_text, str):
                    text = " ".join(block_text.split())
                    if text:
                        return text[:80]
    return ""


def _expand_input(
    text: str,
    workspace: Path,
    skills: Sequence[Skill],
) -> str | None:
    attachments: list[str] = []
    skill_tokens = re.findall(r"(?<!\S)\$([A-Za-z0-9_.-]+)", text)
    for name in skill_tokens:
        skill = next((item for item in skills if item.name == name), None)
        if skill is None:
            print(f"cade-mini: unknown skill: {name}", file=sys.stderr)
            return None
        attachments.append(f'<skill name="{skill.name}">\n{skill.body}\n</skill>')
    text = re.sub(r"(?<!\S)\$[A-Za-z0-9_.-]+", "", text)
    for raw in re.findall(r"(?<!\S)@(\S+)", text):
        try:
            path = resolve_workspace_path(workspace, raw)
        except ValueError as exc:
            print(f"cade-mini: {exc}", file=sys.stderr)
            return None
        if not path.is_file():
            print(f"cade-mini: file not found: {raw}", file=sys.stderr)
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"cade-mini: cannot read {raw}: {exc}", file=sys.stderr)
            return None
        if len(content) > 50_000:
            content = content[:50_000] + "\n[file truncated]"
        attachments.append(f'<file path="{raw}">\n{content}\n</file>')
    text = re.sub(r"(?<!\S)@\S+", "", text)
    task = " ".join(text.split())
    if attachments:
        block = "\n\n".join(attachments)
        return f"{block}\n\n{task or 'Use the attached context.'}"
    return task


def _run_shell_input(
    command: str,
    tools: Sequence[Tool],
    policy: WorkspacePolicy,
    approve: Callable[[ToolCallBlock], bool | Awaitable[bool]],
) -> None:
    if not command:
        print("usage: !command", file=sys.stderr)
        return
    tool = next((item for item in tools if item.name == "bash"), None)
    if tool is None:
        print("cade-mini: bash tool is unavailable", file=sys.stderr)
        return
    call = ToolCallBlock(
        id=uuid4().hex[:12],
        name="bash",
        args={"command": command},
    )
    decision = policy.check(call)
    if decision == "deny":
        print("permission denied: bash", file=sys.stderr)
        return
    if decision == "ask":
        allowed = approve(call)
        if inspect.isawaitable(allowed):
            allowed = asyncio.run(_await_approval(allowed))
        if not allowed:
            print("permission denied: bash", file=sys.stderr)
            return
    result = asyncio.run(tool.execute(call, None))
    print(result.content)
    if result.is_error:
        print("command failed", file=sys.stderr)


async def _await_approval(value: Awaitable[bool]) -> bool:
    return await value


def _repl(
    log: SessionLog,
    sessions_dir: Path,
    system_prompt: str,
    provider: Provider,
    base_tools: Sequence[Tool],
    skills: Sequence[Skill],
    workspace: Path,
    snapshots: SnapshotStore,
    goals: GoalStore,
    policy: WorkspacePolicy,
    approve: Callable[[ToolCallBlock], bool | Awaitable[bool]],
    approval_router: _ApprovalRouter,
    budget: Budget,
    context_provider: Callable[[], tuple[str, ...]],
    max_steps: int,
    on_event: Callable[[LoopEvent], None],
    compactor: Compactor,
    hooks: HookRunner | None,
    window: Callable[[SessionLog], bool] | None,
    steering: deque[str],
    pending: deque[str],
) -> int:
    tools = _tools_for(base_tools, workspace, log)
    events: queue.Queue[object] = queue.Queue()
    pump = _InputPump(events)
    pump.start()
    if isinstance(approve, AutoReviewer):
        live_approve: Callable[[ToolCallBlock], bool | Awaitable[bool]] = approve
        shell_approve: Callable[[ToolCallBlock], bool | Awaitable[bool]] = approve
    else:
        auto_approve = getattr(approve, "auto_approve", False)
        live_approve = _HostApprover(
            events,
            policy.grants,
            auto_approve=auto_approve,
        )
        shell_approve = _ConsoleApprover(
            events,
            policy.grants,
            auto_approve=auto_approve,
        )
    approval_router.target = live_approve

    def read_line() -> str | None:
        print("cade-mini> ", end="", flush=True)
        while True:
            event = events.get()
            if isinstance(event, _Line):
                return None if event.eof else event.text
            if isinstance(event, _ApprovalRequest):
                event.answer = "n"
                event.done.set()

    print(f"Cade Mini session {log.session_id}. Enter a task, /help, or /exit.")
    while True:
        if pending:
            line = pending.popleft()
            print(f"cade-mini> {line}")
        else:
            line = read_line()
            if line is None:
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
        if text == "/sessions":
            _print_sessions(sessions_dir)
            continue
        if text.startswith("/fork"):
            argument = text[len("/fork") :].strip()
            branch = log.branch()
            users = [entry for entry in branch if entry.type == "user"]
            target = None
            if argument.isdigit():
                index = int(argument)
                if 1 <= index <= len(users):
                    target = users[-index]
            elif argument:
                target = next(
                    (entry for entry in branch if entry.id == argument),
                    None,
                )
            elif users:
                target = users[-1]
            if target is None:
                print("cade-mini: no user turn to fork", file=sys.stderr)
                continue
            try:
                log = log.copy_branch(
                    sessions_dir,
                    before_entry_id=target.id,
                    project_root=workspace,
                )
            except LogError as exc:
                print(f"cade-mini: {exc}", file=sys.stderr)
                continue
            tools = _tools_for(base_tools, workspace, log)
            print(f"session {log.session_id}")
            print(f"forked before: {_entry_preview(target)}")
            continue
        if text == "/clone":
            log = log.copy_branch(
                sessions_dir,
                project_root=workspace,
            )
            tools = _tools_for(base_tools, workspace, log)
            print(f"session {log.session_id}")
            continue
        if text == "/tree":
            active = {entry.id for entry in log.branch()}
            for entry in log.entries():
                mark = "*" if entry.id in active else " "
                print(f"{mark} {entry.id} {entry.type:<12} {_entry_preview(entry)}")
            continue
        if text.startswith("/rewind"):
            argument = text[len("/rewind") :].strip() or "1"
            try:
                count = int(argument)
            except ValueError:
                print("usage: /rewind [N]", file=sys.stderr)
                continue
            if count <= 0:
                print("usage: /rewind [N]", file=sys.stderr)
                continue
            log = log.copy_branch(
                sessions_dir,
                drop_recent_users=count,
                project_root=workspace,
            )
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
        if text.startswith("/undo"):
            argument = text[len("/undo") :].strip() or "1"
            try:
                count = int(argument)
            except ValueError:
                print("usage: /undo [N]", file=sys.stderr)
                continue
            if count <= 0:
                print("usage: /undo [N]", file=sys.stderr)
                continue
            for _ in range(count):
                try:
                    snapshot = snapshots.undo_last()
                except (OSError, ValueError) as exc:
                    print(f"cade-mini: {exc}", file=sys.stderr)
                    break
                if snapshot is None:
                    print("no snapshots to undo")
                    break
                print(f"restored {snapshot.path}")
            continue
        if text.startswith("/steer"):
            value = text[len("/steer") :].strip()
            if not value:
                print("usage: /steer TEXT", file=sys.stderr)
            else:
                steering.append(value)
                print("guidance queued for the next model step")
            continue
        if text.startswith("/queue"):
            value = text[len("/queue") :].strip()
            if not value:
                print("usage: /queue TEXT", file=sys.stderr)
            else:
                pending.append(value)
                print("task queued")
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
            try:
                with log.run_lock():
                    compacted = asyncio.run(compactor(log, budget))
            except LogError as exc:
                print(f"cade-mini: {exc}", file=sys.stderr)
                continue
            print("compacted" if compacted else "nothing to compact")
            continue
        if text.startswith("/new-context"):
            handoff = text[len("/new-context") :].strip() or None
            try:
                with log.run_lock():
                    started = reset_window(log, handoff=handoff, reason="manual")
            except LogError as exc:
                print(f"cade-mini: {exc}", file=sys.stderr)
                continue
            print("window started" if started else "no task to reset")
            continue

        if text.startswith("!"):
            _run_shell_input(text[1:].strip(), tools, policy, shell_approve)
            continue
        expanded = _expand_input(text, workspace, skills)
        if expanded is None:
            continue
        text = expanded

        cancel = CancellationToken()

        def worker(
            _log: SessionLog = log,
            _text: str = text,
            _tools: Sequence[Tool] = tools,
            _cancel: CancellationToken = cancel,
        ) -> None:
            try:
                result = _run_task(
                    _log,
                    _text,
                    provider,
                    _tools,
                    policy,
                    live_approve,
                    budget,
                    context_provider(),
                    max_steps,
                    on_event,
                    compactor,
                    hooks,
                    window,
                    lambda: steering.popleft() if steering else None,
                    _cancel,
                )
            except Exception as exc:  # noqa: BLE001
                result = RunResult(
                    answer="",
                    steps=0,
                    termination="provider_error",
                    error=str(exc),
                )
            events.put(_TaskDone(result))

        result, exit_requested = _run_task_live(
            events,
            pending,
            steering,
            cancel,
            threading.Thread(target=worker, daemon=True),
        )
        if exit_requested:
            return 0
        print()
        if result.error:
            print(f"[{result.termination}] {result.error}", file=sys.stderr)


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
    elif isinstance(event, ReasoningDeltaEvent):
        payload = {"type": "reasoning", "text": event.text}
    else:
        payload = {
            "type": "usage",
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
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
