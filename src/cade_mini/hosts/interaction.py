"""Host-independent session, model, and command operations for the terminal."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from cade_mini.ai.events import FinalMessage, ProviderEvent, ProviderFailure, TextDelta
from cade_mini.ai.models import (
    get_model_context_window,
    get_model_reasoning_efforts,
    get_models,
)
from cade_mini.app.provider import ProviderConfig, build_provider
from cade_mini.core.compaction import Compactor
from cade_mini.core.log import SessionLog, list_sessions, session_title
from cade_mini.core.loop import (
    CancellationToken,
    LoopEvent,
    Provider,
    RunResult,
    Tool,
    ToolEndEvent,
    ToolResult,
    ToolStartEvent,
    run,
)
from cade_mini.core.projection import project
from cade_mini.core.request import Budget, build
from cade_mini.core.types import ToolCallBlock, UserMessage
from cade_mini.core.window import reset_window
from cade_mini.extensions.goal import GoalStore
from cade_mini.extensions.hooks import HookRunner
from cade_mini.extensions.skills import Skill
from cade_mini.extensions.snapshots import SnapshotStore
from cade_mini.extensions.subagents import DelegateTool, SubagentTool
from cade_mini.hosts.command_catalog import COMMANDS, command_help
from cade_mini.hosts.context_report import format_context_usage
from cade_mini.hosts.interaction_question import QuestionTool
from cade_mini.hosts.interaction_settings import AuthenticationService, SettingsService
from cade_mini.safety.policy import WorkspacePolicy, filter_tools
from cade_mini.safety.review import AutoReviewer
from cade_mini.tools.history import HistoryTool
from cade_mini.tools.workspace import resolve_workspace_path


class UnavailableProvider:
    """Explicit disconnected terminal state; never substitutes a model call."""

    def __init__(self, model: str, transport: str, reason: str) -> None:
        self.model = model
        self.transport = transport
        self.reason = reason
        self.context_window = get_model_context_window(model)

    async def stream(
        self, messages: Any, tools: Any, options: Any = None
    ) -> AsyncIterator[ProviderEvent]:
        yield ProviderFailure(
            message=self.reason,
            exception_type="AuthenticationRequired",
        )


class ApprovalRouter(Protocol):
    """Bridge nested agent approval requests back into the active host."""

    target: Callable[[ToolCallBlock], bool | Awaitable[bool]]


@dataclass(frozen=True)
class CommandResult:
    lines: tuple[str, ...] = ()
    picker: str | None = None
    changed_session: bool = False
    draft: str | None = None
    quit: bool = False
    task: str | None = None
    side_question: str | None = None
    login_provider: str | None = None
    config_field: str | None = None


def _count(value: str) -> int:
    number = int(value or "1")
    if number <= 0:
        raise ValueError("Expected a positive count")
    return number


def _size(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", value)
    if match is None:
        raise ValueError("Use an integer, 200k or 1.5m")
    size = int(float(match[1]) * {"": 1, "k": 1000, "m": 1000000}[match[2].lower()])
    if size <= 0:
        raise ValueError("Context window must be positive")
    return size


def preview(entry: Any) -> str:
    for key in ("text", "content", "summary"):
        item = entry.data.get(key)
        if isinstance(item, str):
            return " ".join(item.split())[:100]
    if entry.type == "assistant":
        blocks = entry.data.get("blocks", [])
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                return " ".join(block["text"].split())[:100]
    return ""


class InteractionService:
    """Stateful application facade; no terminal imports or terminal side effects."""

    def __init__(
        self,
        *,
        log: SessionLog,
        sessions_dir: Path,
        system_prompt: str,
        provider: Provider,
        provider_config: ProviderConfig,
        tools: Sequence[Tool],
        skills: Sequence[Skill],
        workspace: Path,
        snapshots: SnapshotStore,
        goals: GoalStore,
        policy: WorkspacePolicy,
        budget: Budget,
        context: Callable[[], Sequence[str]],
        max_steps: int,
        compactor: Compactor,
        hooks: HookRunner | None,
        window: Callable[[SessionLog], bool] | None,
        approval_router: ApprovalRouter | None = None,
        automatic_approver: Callable[[ToolCallBlock], bool | Awaitable[bool]]
        | None = None,
        config_path: Path | None = None,
        context_window_explicit: bool = False,
    ) -> None:
        self.log, self.sessions_dir, self.system_prompt = (
            log,
            sessions_dir,
            system_prompt,
        )
        self.provider, self.provider_config = provider, provider_config
        self.base_tools, self.skills = tuple(tools), tuple(skills)
        self.workspace, self.snapshots, self.goals = workspace, snapshots, goals
        self.policy, self.budget, self.context = policy, budget, context
        self.context_window_explicit = context_window_explicit
        self.max_steps, self.compactor, self.hooks, self.window = (
            max_steps,
            compactor,
            hooks,
            window,
        )
        self.settings = SettingsService(workspace, config_path)
        self.auth = AuthenticationService()
        self.steering: deque[str] = deque()
        self.pending: deque[str] = deque()
        self.busy_mode = "followup"
        self.running = False
        self.cancel: CancellationToken | None = None
        self.last_usage: dict[str, int] = {}
        self.question_tool: QuestionTool | None = None
        self.approval_router = approval_router
        self.automatic_approver = automatic_approver

    @property
    def tools(self) -> tuple[Tool, ...]:
        regular: tuple[Tool, ...] = (
            *self.base_tools,
            HistoryTool(self.workspace, self.log),
        )
        return (*regular, self.question_tool) if self.question_tool else regular

    def choices(self, kind: str) -> tuple[tuple[str, str], ...]:
        if kind == "commands":
            return tuple(COMMANDS.items())
        if kind == "fork":
            users = [entry for entry in self.log.branch() if entry.type == "user"]
            return tuple(
                (
                    item.id,
                    f"{index}. {preview(item)}",
                )
                for index, item in enumerate(reversed(users[-40:]), 1)
            )
        if kind == "sessions":
            return tuple(
                (session.id, f"{session.title[:65]}  ·  {session.id[:8]}")
                for session in list_sessions(self.sessions_dir, include_empty=False)
            )
        if kind == "tree":
            active = {e.id for e in self.log.branch()}
            return tuple(
                (e.id, f"{'*' if e.id in active else ' '} {e.type:<12} {preview(e)}")
                for e in self.log.entries()
                if e.type
                in {"user", "assistant", "tool_result", "window", "compaction"}
            )
        if kind == "config":
            return self.settings.choices()
        if kind in {"login", "logout"}:
            return self.auth.choices()
        if kind == "model":
            providers = self.settings.configured_providers(self.auth.store)
            models = get_models("openai")
            items = [
                (
                    f"{provider}/{model.id}",
                    f"{model.id} · {provider}",
                )
                for provider in providers
                for model in models
            ]
            current = f"{self.provider_config.provider}/{self.provider.model}"
            if (
                not isinstance(self.provider, UnavailableProvider)
                and self.provider_config.provider in providers
                and current not in {name for name, _ in items}
            ):
                items.insert(0, (current, f"{self.provider.model} · configured"))
            return tuple(items)
        if kind == "effort":
            return tuple(
                (e, e)
                for e in (
                    get_model_reasoning_efforts(self.provider.model)
                    or ("none", "low", "medium", "high", "xhigh")
                )
            )
        if kind == "queue":
            return tuple(
                (name, description)
                for name, description in (
                    ("followup", "Queue input for after the current turn"),
                    ("steer", "Guide the model on its next step"),
                    ("interrupt", "Stop the current task and replace it"),
                )
            )
        if kind == "thinking":
            return (
                ("on", "Show reasoning summaries"),
                ("off", "Hide reasoning summaries"),
            )
        if kind == "auth":
            return (
                ("status", "List connected accounts"),
                ("login", "Connect a provider"),
                ("logout", "Disconnect a provider"),
            )
        if kind == "goal":
            return (
                ("pause", "Pause the active goal"),
                ("resume", "Resume a paused goal"),
                ("done", "Mark goal complete"),
                ("clear", "Remove workspace goal"),
            )
        if kind == "context-window":
            return (("default", "Use the selected model limit"),)
        if kind == "mode":
            return tuple(
                (mode, f"{mode}{' (current)' if mode == self.policy.mode else ''}")
                for mode in ("act", "build", "plan")
            )
        if kind == "skill":
            return tuple((s.name, s.name) for s in self.skills)
        if kind == "tool":
            return tuple(
                (tool.name, f"{tool.name:<16} {tool.description[:92]}")
                for tool in self.tools
            )
        if kind == "permissions":
            return (("list", "List grants"), ("clear", "Clear session grants"))
        return ()

    def _new(self) -> None:
        self.log = SessionLog.create(self.sessions_dir, project_root=self.workspace)
        self.log.append("system", {"text": self.system_prompt})

    def _resume(self, session_id: str) -> None:
        if not session_id:
            sessions = list_sessions(self.sessions_dir)
            if not sessions:
                raise ValueError("No sessions to resume")
            session_id = sessions[0].id
        self.log = SessionLog.open(self.sessions_dir / f"session-{session_id}.jsonl")
        if not any(e.type == "system" for e in self.log.branch()):
            self.log.append("system", {"text": self.system_prompt})

    def _turn(self, argument: str) -> Any:
        users = [e for e in self.log.branch() if e.type == "user"]
        if not argument:
            return users[-1] if users else None
        if argument.isdigit():
            n = int(argument)
            return users[-n] if 1 <= n <= len(users) else None
        return next((e for e in users if e.id == argument), None)

    def select_model(self, choice: str, effort: str | None = None) -> str:
        provider_name = self.provider_config.provider
        model = choice.strip()
        if "/" in model:
            provider_name, model = model.split("/", 1)
        if not provider_name or not model:
            raise ValueError("Select a provider/model")
        base_url = (
            self.provider_config.base_url
            if provider_name == self.provider_config.provider
            else ""
        )
        supported = get_model_reasoning_efforts(model)
        if effort is not None and supported and effort not in supported:
            raise ValueError(
                f"{model} supports reasoning efforts: {', '.join(supported)}"
            )
        reasoning_effort = (
            effort if effort is not None else self.provider_config.reasoning_effort
        )
        if supported and reasoning_effort not in supported:
            reasoning_effort = "high" if "high" in supported else supported[0]
        config = ProviderConfig(
            provider=provider_name,
            model=model,
            base_url=base_url,
            thinking=self.provider_config.thinking,
            reasoning_effort=reasoning_effort,
        )
        provider = build_provider(config)
        self._install_provider(config, provider)
        return f"Model: {provider_name}/{provider.model} · {config.reasoning_effort}"

    def _install_provider(self, config: ProviderConfig, provider: Provider) -> None:
        """Switch the foreground and delegated model as one operation."""
        window = getattr(provider, "context_window", None) or get_model_context_window(
            provider.model
        )
        if (
            self.context_window_explicit
            and isinstance(window, int)
            and window > 0
            and self.budget.context_window > window
        ):
            raise ValueError(
                f"Configured context window {self.budget.context_window:,} "
                f"exceeds {provider.model} maximum {window:,}"
            )
        budget = (
            self.budget
            if self.context_window_explicit
            else Budget(context_window=window)
            if isinstance(window, int) and window > 0
            else self.budget
        )
        self.provider_config, self.provider, self.budget = config, provider, budget
        self.compactor = Compactor(
            provider,
            config=self.compactor.config,
            extra_context=self.compactor.extra_context,
        )
        if isinstance(self.automatic_approver, AutoReviewer):
            self.automatic_approver.provider = provider
        for tool in self.base_tools:
            if isinstance(tool, (SubagentTool, DelegateTool)):
                tool.runner.provider = provider
                tool.runner.budget = budget

    def expand(self, message: str) -> str:
        additions: list[str] = []
        for name in re.findall(r"(?<!\S)\$([A-Za-z0-9_.-]+)", message):
            skill = next((s for s in self.skills if s.name == name), None)
            if skill is None:
                raise ValueError(f"Unknown skill: {name}")
            additions.append(f'<skill name="{name}">\n{skill.body}\n</skill>')
        message = re.sub(r"(?<!\S)\$[A-Za-z0-9_.-]+", "", message)
        for path_text in re.findall(r"(?<!\S)@(\S+)", message):
            path = resolve_workspace_path(self.workspace, path_text)
            if not path.is_file():
                raise ValueError(f"File does not exist: {path_text}")
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > 50_000:
                content = content[:50_000] + "\n[file truncated]"
            additions.append(f'<file path="{path_text}">\n{content}\n</file>')
        message = re.sub(r"(?<!\S)@\S+", "", message).strip()
        return (
            "\n\n".join([*additions, message or "Use attached context."])
            if additions
            else message
        )

    async def run_task(
        self,
        text: str,
        on_event: Callable[[LoopEvent], None],
        approve: Callable[[ToolCallBlock], Awaitable[bool]],
    ) -> RunResult:
        if self.running:
            raise RuntimeError("A task is already running")
        if isinstance(self.provider, UnavailableProvider):
            raise RuntimeError(self.provider.reason)  # noqa: TRY004 - authentication state
        self.running = True
        self.cancel = CancellationToken()
        try:
            result = await run(
                self.log,
                text,
                provider=self.provider,
                tools=filter_tools(self.tools, self.policy.mode),
                budget=self.budget,
                policy=self.policy,
                approve=approve,
                context=self.context(),
                max_steps=self.max_steps,
                compact=self.compactor,
                window=self.window,
                hooks=self.hooks,
                steering=lambda: self.steering.popleft() if self.steering else None,
                cancel=self.cancel,
                on_event=on_event,
            )
            self.last_usage = result.usage or {}
            return result
        finally:
            self.cancel = None
            self.running = False

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        approve: Callable[[ToolCallBlock], Awaitable[bool]],
        on_event: Callable[[LoopEvent], None] | None = None,
    ) -> ToolResult:
        """Interactive tools use the same policy, hooks and lifecycle as the loop."""
        call = ToolCallBlock(id=uuid4().hex[:12], name=name, args=args)

        def finish(result: ToolResult) -> ToolResult:
            if on_event is not None:
                on_event(ToolEndEvent(call.id, result.content, result.is_error))
            return result

        if on_event is not None:
            on_event(ToolStartEvent(call.id, call.name, call.args))
        decision = self.policy.check(call)
        if decision == "deny" or (decision == "ask" and not await approve(call)):
            return finish(
                ToolResult(content=f"Permission denied: {name}", is_error=True)
            )
        tool = next(
            (t for t in filter_tools(self.tools, self.policy.mode) if t.name == name),
            None,
        )
        if tool is None:
            return finish(ToolResult(content=f"Unknown tool: {name}", is_error=True))
        try:
            if self.hooks is not None:
                allowed, detail = await self.hooks.before(call)
                if not allowed:
                    return finish(
                        ToolResult(
                            content=f"Hook denied {name}: {detail}".strip(),
                            is_error=True,
                        )
                    )
            result = await tool.execute(call, None)
            if self.hooks is not None:
                await self.hooks.after(call, result.content, result.is_error)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(content=f"Tool error: {exc}", is_error=True)
        return finish(result)

    async def side_question(
        self,
        question: str,
        on_text: Callable[[str], None],
    ) -> str:
        """Read a context copy with a fresh provider; no tools or session writes."""
        if not question.strip():
            raise ValueError("Side question must not be empty")
        provider = build_provider(self.provider_config)
        context = self.context()
        conversation = [*project(self.log.branch()), UserMessage(text=question)]
        request = build(conversation, tools=(), budget=self.budget, context=context)
        if request.report.over_budget:
            raise ValueError("Side question exceeds context budget")
        text_parts: list[str] = []
        final_reason = ""
        failure: str | None = None
        async for event in provider.stream(list(request.messages), [], None):
            if isinstance(event, TextDelta):
                text_parts.append(event.chunk)
                on_text(event.chunk)
            elif isinstance(event, ProviderFailure):
                failure = event.message
                break
            elif isinstance(event, FinalMessage):
                final_reason = event.stop_reason
                if not text_parts and event.content:
                    text_parts.append(event.content)
                    on_text(event.content)
        if failure is not None:
            raise RuntimeError(failure)
        if not final_reason:
            raise RuntimeError("Side question stream ended without a final response")
        if final_reason in {"error", "cancelled", "aborted"}:
            raise RuntimeError(f"Side question ended: {final_reason}")
        return "".join(text_parts)

    async def command(self, line: str) -> CommandResult:
        command, _, rest = line.strip().partition(" ")
        arg = rest.strip()
        if command in {"/quit", "/exit"}:
            return CommandResult(quit=True)
        if command == "/help":
            return CommandResult(command_help())
        if command in {"/new", "/clear"}:
            self._new()
            return CommandResult(
                (f"Session: {self.log.session_id}",), changed_session=True
            )
        if command in {"/resume", "/continue", "/sessions"}:
            if command == "/sessions" or (command == "/resume" and not arg):
                return CommandResult(picker="sessions")
            self._resume(arg)
            return CommandResult(
                (f"Session: {self.log.session_id}",), changed_session=True
            )
        if command == "/rename":
            if not arg:
                return CommandResult(
                    draft="/rename " + session_title(self.log.branch())
                )
            title = " ".join(arg.split())
            if not 1 <= len(title) <= 120 or any(ord(ch) < 32 for ch in title):
                raise ValueError("Session title must be 1-120 visible characters")
            initial = next(
                (entry for entry in self.log.branch() if entry.type == "meta"),
                None,
            )
            if initial is None:
                raise ValueError("Session metadata is missing")
            self.log.append("meta", {**initial.data, "title": title})
            return CommandResult((f"Session renamed: {title}",))
        if command == "/tree":
            if not arg:
                return CommandResult(picker="tree")
            entry = next((e for e in self.log.branch() if e.id == arg), None)
            if entry is None:
                raise ValueError(f"Session event not found: {arg}")
            details = json.dumps(entry.data, ensure_ascii=False, indent=2)
            return CommandResult((f"{entry.type} · {entry.id}\n{details}",))
        if command in {"/clone", "/fork", "/rewind"}:
            if command == "/fork" and not arg:
                return CommandResult(picker="fork")
            draft = None
            if command == "/fork":
                turn = self._turn(arg)
                if turn is None:
                    raise ValueError("No matching user turn")
                draft = str(turn.data.get("text", ""))
                copied = self.log.copy_branch(
                    self.sessions_dir,
                    before_entry_id=turn.id,
                    project_root=self.workspace,
                )
            elif command == "/rewind":
                copied = self.log.copy_branch(
                    self.sessions_dir,
                    drop_recent_users=_count(arg),
                    project_root=self.workspace,
                )
            else:
                copied = self.log.copy_branch(
                    self.sessions_dir, project_root=self.workspace
                )
            self.log = copied
            return CommandResult(
                (f"Session: {copied.session_id}",), changed_session=True, draft=draft
            )
        if command == "/mode":
            if not arg:
                return CommandResult(picker="mode")
            self.policy.set_mode(arg)
            return CommandResult((f"Mode: {arg}",))
        if command == "/model":
            return (
                CommandResult((self.select_model(arg),))
                if arg
                else CommandResult(picker="model")
            )
        if command == "/effort":
            if not arg:
                return CommandResult(picker="effort")
            return CommandResult((self.select_model(self.provider_config.model, arg),))
        if command == "/thinking":
            if arg and arg not in {"on", "off"}:
                raise ValueError("Usage: /thinking [on|off]")
            self.provider_config = ProviderConfig(
                provider=self.provider_config.provider,
                model=self.provider_config.model,
                base_url=self.provider_config.base_url,
                reasoning_effort=self.provider_config.reasoning_effort,
                thinking=(arg == "on") if arg else not self.provider_config.thinking,
            )
            config = self.provider_config
            provider = build_provider(config)
            self._install_provider(config, provider)
            return CommandResult((f"Thinking: {config.thinking}",))
        if command == "/context":
            return CommandResult(format_context_usage(self))
        if command == "/context-window":
            if not arg:
                return CommandResult((f"Window: {self.budget.context_window:,}",))
            available = getattr(self.provider, "context_window", None)
            size = (available or 200_000) if arg == "default" else _size(arg)
            if isinstance(available, int) and available > 0 and size > available:
                raise ValueError(f"Maximum model context: {available:,}")
            self.budget = Budget(context_window=size)
            self.context_window_explicit = arg != "default"
            for tool in self.base_tools:
                if isinstance(tool, (SubagentTool, DelegateTool)):
                    tool.runner.budget = self.budget
            return CommandResult((f"Window: {size:,}",))
        if command == "/compact":
            with self.log.run_lock():
                ok = await self.compactor(self.log, self.budget)
            return CommandResult(
                ("Compacted" if ok else "Nothing to compact",), changed_session=ok
            )
        if command in {"/rollover", "/new-context"}:
            if arg and arg != "--force":
                raise ValueError("Usage: /rollover [--force]")
            working_note = self.workspace / "NOTE.md"
            if arg != "--force" and not working_note.is_file():
                message = (
                    "Write NOTE.md with the current goal, decisions, "
                    "verification status and next action before rollover; "
                    "use /rollover --force to continue without it."
                )
                return CommandResult((message,))
            handoff = (
                working_note.read_text(encoding="utf-8")
                if working_note.is_file()
                else None
            )
            with self.log.run_lock():
                ok = reset_window(self.log, handoff=handoff, reason="manual")
            return CommandResult(
                ("Context reset" if ok else "No task to reset",), changed_session=ok
            )
        if command == "/permissions":
            if not arg:
                return CommandResult(picker="permissions")
            if arg == "clear":
                self.policy.grants.clear()
                return CommandResult(("Permissions cleared",))
            if arg == "list":
                return CommandResult(
                    tuple(self.policy.grants.describe()) or ("No grants",)
                )
            raise ValueError("Usage: /permissions [list|clear]")
        if command in {"/undo", "/revert"}:
            if arg == "--list":
                snapshots = self.snapshots.list()
                return CommandResult(
                    tuple(
                        f"{item.created_at}  {item.path}"
                        for item in reversed(snapshots)
                    )
                    or ("No snapshots available",)
                )
            messages = []
            for _ in range(_count(arg)):
                restored = self.snapshots.undo_last()
                if restored is None:
                    break
                messages.append(f"Restored {restored.path}")
            return CommandResult(tuple(messages) or ("No snapshots",))
        if command == "/goal":
            if not arg:
                goal = self.goals.load()
                return CommandResult(
                    ("No goal" if goal is None else f"{goal.status}: {goal.objective}",)
                )
            if arg == "clear":
                self.goals.clear()
                return CommandResult(("Goal cleared",))
            if arg == "done":
                goal = self.goals.complete()
            elif arg == "pause":
                goal = self.goals.pause()
            elif arg == "resume":
                goal = self.goals.resume()
            else:
                goal = self.goals.set(arg)
            return CommandResult(
                (f"{goal.status}: {goal.objective}" if goal else "No goal is set",)
            )
        if command == "/btw":
            if not arg:
                raise ValueError("Usage: /btw QUESTION")
            return CommandResult(side_question=arg)
        if command == "/steer":
            if not arg:
                raise ValueError("Usage: /steer MESSAGE")
            if not self.running:
                return CommandResult(task=arg)
            self.steering.append(arg)
            return CommandResult(("Guidance queued for next model step",))
        if command == "/queue":
            if not arg:
                return CommandResult(picker="queue")
            if arg in {"followup", "steer", "interrupt"}:
                self.busy_mode = arg
                return CommandResult((f"Busy input mode: {arg}",))
            if arg.startswith("steer "):
                self.steering.append(arg[6:])
                return CommandResult(("Guidance queued",))
            self.pending.append(arg)
            return CommandResult((f"Queued: {len(self.pending)}",))
        if command == "/stop":
            if self.cancel:
                self.cancel.cancel("Stopped by user")
            return CommandResult(
                ("Stop requested" if self.cancel else "No running task",)
            )
        if command == "/skill":
            return (
                CommandResult(task="$" + arg) if arg else CommandResult(picker="skill")
            )
        if command == "/tool":
            if not arg:
                return CommandResult(picker="tool")
            if arg == "list":
                return CommandResult(
                    tuple(f"{tool.name} · {tool.description}" for tool in self.tools)
                )
            return CommandResult(("Use /tool NAME JSON to invoke a tool.",))
        if command == "/mcp":
            return CommandResult(
                tuple(t.name for t in self.tools if t.name.startswith("mcp__"))
                or ("No MCP tools",)
            )
        if command == "/hooks":
            return CommandResult(
                (f"Hooks: {type(self.hooks).__name__ if self.hooks else 'none'}",)
            )
        if command == "/memory":
            return CommandResult((str(self.workspace / "MEMORY.md"),))
        if command == "/config":
            if not arg:
                return CommandResult(picker="config")
            name, sep, value = arg.partition("=")
            if not sep:
                self.settings.edit_command(name.strip())
                return CommandResult(config_field=name.strip())
            return CommandResult((self.settings.save(name.strip(), value.strip()),))
        if command == "/auth":
            sub, _, details = arg.partition(" ")
            if sub in {"", "status"}:
                return CommandResult(self.auth.status())
            if sub in {"login", "connect"}:
                return await self.command("/login " + details)
            if sub == "logout":
                return await self.command("/logout " + details)
            raise ValueError("Usage: /auth [status|login|logout]")
        if command in {"/login", "/connect"}:
            if not arg:
                return CommandResult(picker="login")
            if arg not in self.auth.PROVIDERS:
                raise ValueError(f"Unknown provider: {arg}")
            return CommandResult(login_provider=arg)
        if command == "/logout":
            if not arg:
                return CommandResult(picker="logout")
            message = self.auth.logout(arg)
            if arg == self.provider_config.provider:
                try:
                    provider = build_provider(self.provider_config)
                except (RuntimeError, ValueError) as exc:
                    provider = UnavailableProvider(
                        model=self.provider_config.model,
                        transport=arg,
                        reason=str(exc),
                    )
                self._install_provider(self.provider_config, provider)
            return CommandResult((message,))
        return CommandResult((f"Unknown command: {command}. Type /help.",))
