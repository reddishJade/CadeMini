"""应用级会话、任务和命令操作；单次任务与 TUI 共用。"""

from __future__ import annotations

import re
import shlex
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cade.ai.events import FinalMessage, ProviderEvent, ProviderFailure, TextDelta
from cade.ai.models import (
    get_model_context_window,
    get_model_reasoning_efforts,
    get_models,
)
from cade.app.commands import COMMANDS, command_help
from cade.app.context_report import ContextUsage, inspect_context_usage
from cade.app.provider import ProviderConfig, build_provider
from cade.app.settings import AuthenticationService, SettingsService
from cade.coding.goal import GoalStore
from cade.coding.mcp.tools import McpRegistry
from cade.coding.security.policy import WorkspacePolicy, filter_tools
from cade.coding.security.review import AutoReviewer
from cade.coding.skills.context import restore_skill_context
from cade.coding.skills.models import SkillDef
from cade.coding.skills.tools import LoadSkillTool
from cade.coding.subagents import DelegateTool
from cade.coding.tools.base import resolve_workspace_path
from cade.coding.tools.history import HistoryTool
from cade.coding.tools.question import QuestionTool
from cade.coding.workspace.recovery.coordinator import WorkspaceRecovery
from cade.coding.workspace.recovery.store import Scope
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.cancellation import CancellationToken
from cade.harness.compaction import Compactor
from cade.harness.events import LoopEvent, RunResult, ToolEndEvent
from cade.harness.executor import execute_tool, index_tools
from cade.harness.loop import run
from cade.harness.permissions import ApprovalRequest, ApprovalResult, Approver
from cade.harness.provider import Provider
from cade.harness.request import Budget, build
from cade.harness.session.history import HistoryNode, history_tree
from cade.harness.session.log import SessionLog, list_sessions
from cade.harness.session.projection import project
from cade.harness.session.types import JsonValue, LogEntry, ToolCallBlock, UserMessage
from cade.harness.tools import Tool, ToolResult
from cade.harness.window import reset_window


class UnavailableProvider:
    """显式表示未连接状态，不用替代回答掩盖缺少认证。"""

    def __init__(self, model: str, transport: str, reason: str) -> None:
        self.model = model
        self.transport = transport
        self.reason = reason
        self.context_window = get_model_context_window(model) or 0

    async def stream(
        self, messages: Any, tools: Any, options: Any = None
    ) -> AsyncIterator[ProviderEvent]:
        yield ProviderFailure(
            message=self.reason,
            exception_type="AuthenticationRequired",
        )


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
    context_usage: ContextUsage | None = None


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


class ApplicationSession:
    """管理交互状态与应用操作，不导入终端模块或操作终端。"""

    def __init__(
        self,
        *,
        log: SessionLog,
        sessions_dir: Path,
        system_prompt: str,
        provider: Provider,
        provider_config: ProviderConfig,
        tools: Sequence[Tool],
        skills: Sequence[SkillDef],
        workspace: Path,
        snapshots: SnapshotStore,
        goals: GoalStore,
        policy: WorkspacePolicy,
        budget: Budget,
        context: Callable[[], Sequence[str]],
        max_steps: int,
        compactor: Compactor,
        window: Callable[[SessionLog], bool] | None,
        automatic_approver: Approver | None = None,
        config_path: Path | None = None,
        context_window_explicit: bool = False,
        recovery: WorkspaceRecovery | None = None,
        mcp: McpRegistry | None = None,
    ) -> None:
        self.log, self.sessions_dir, self.system_prompt = (
            log,
            sessions_dir,
            system_prompt,
        )
        self.provider, self.provider_config = provider, provider_config
        self.base_tools, self.skills = tuple(tools), tuple(skills)
        self.mcp = mcp
        self.workspace, self.snapshots, self.goals = workspace, snapshots, goals
        self.recovery = recovery or WorkspaceRecovery(
            snapshots, system_prompt=system_prompt, read_only=policy.read_only
        )
        self.policy, self.budget, self.context = policy, budget, context
        self.context_window_explicit = context_window_explicit
        self.max_steps, self.compactor, self.window = (
            max_steps,
            compactor,
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
        self.automatic_approver = automatic_approver

    @property
    def tools(self) -> tuple[Tool, ...]:
        regular: tuple[Tool, ...] = (
            *(
                tool
                for tool in self.base_tools
                if self.mcp is None or not tool.name.startswith("mcp__")
            ),
            *(self.mcp.tools if self.mcp is not None else ()),
            HistoryTool(self.workspace, self.log),
        )
        return (*regular, self.question_tool) if self.question_tool else regular

    def history(self) -> tuple[HistoryNode, ...]:
        """返回完整会话树，活动路径和非活动路径共用同一读取快照。"""
        return history_tree(self.log.entries())

    def choices(self, kind: str) -> tuple[tuple[str, str], ...]:
        if kind == "commands":
            return tuple(COMMANDS.items())
        if kind == "fork":
            users = [entry for entry in self.log.entries() if entry.type == "user"]
            return tuple(
                (
                    item.id,
                    f"{index}. {preview(item)}",
                )
                for index, item in enumerate(reversed(users), 1)
            )
        if kind in {"rewind", "revert"}:
            return tuple(
                (entry.id, preview(entry))
                for entry in self.log.branch()
                if entry.type == "user" and entry.data.get("source", "user") == "user"
            )
        if kind == "tree":
            return tuple(
                (
                    node.entry.id,
                    f"{'  ' * min(node.depth, 12)}{node.entry.type}: {preview(node.entry)}"
                    + (" (current)" if node.current else ""),
                )
                for node in self.history()
            )
        if kind == "sessions":
            return tuple(
                (session.id, f"{session.title[:65]}  ·  {session.id[:8]}")
                for session in list_sessions(self.sessions_dir)
            )
        if kind == "config":
            return self.settings.choices()
        if kind in {"login", "logout"}:
            return self.auth.choices()
        if kind == "model":
            providers = self.settings.configured_providers(self.auth.store)
            items = [
                (
                    f"{provider}/{model.id}",
                    f"{model.id} · {provider}",
                )
                for provider in providers
                for model in get_models(provider)
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
            return tuple(
                ("off" if level == "none" else level, level)
                for level in get_model_reasoning_efforts(self.provider.model)
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
                for mode in ("ask", "build", "plan")
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
        self.log = SessionLog.create(
            self.sessions_dir, project_root=self.workspace, deferred=True
        )
        self.log.append("system", {"text": self.system_prompt})
        self.policy.grants.clear()
        self.last_usage = {}

    def _resume(self, session_id: str) -> None:
        if not session_id:
            sessions = list_sessions(self.sessions_dir)
            if not sessions:
                raise ValueError("No sessions to resume")
            session_id = sessions[0].id
        self.log = SessionLog.open(self.sessions_dir / f"session-{session_id}.jsonl")
        self.recovery.recover(self.log)
        if not any(e.type == "system" for e in self.log.branch()):
            self.log.append("system", {"text": self.system_prompt})
        self.policy.grants.clear()
        self.last_usage = {}

    def resolve_turn(self, argument: str) -> LogEntry | None:
        users = [e for e in self.log.entries() if e.type == "user"]
        if not argument:
            return users[-1] if users else None
        selected = next((entry for entry in users if entry.id == argument), None)
        if selected is not None:
            return selected
        if argument.isdigit():
            n = int(argument)
            return users[-n] if 1 <= n <= len(users) else None
        return None

    def recovery_arguments(
        self, line: str
    ) -> tuple[str, Scope, str | None, list[str] | None]:
        """解析恢复命令；UI 预览和执行共用同一组参数。"""
        parts = shlex.split(line)
        command, args = parts[0], parts[1:]
        if not args:
            raise ValueError("Select a historical user prompt")
        turn = self.resolve_turn(args[0])
        if turn is None:
            raise ValueError("User prompt not found")
        if command == "/rewind":
            if len(args) != 2 or args[1] not in {"conversation", "code", "both"}:
                raise ValueError("Usage: /rewind ENTRY conversation|code|both")
            return turn.id, cast(Scope, args[1]), None, None
        if command != "/revert":
            raise ValueError("Unknown recovery command")
        separator = args.index("--") if "--" in args else len(args)
        if separator not in {1, 2}:
            raise ValueError("Usage: /revert START [END] [-- PATH ...]")
        end = self.resolve_turn(args[1]) if separator == 2 else None
        if separator == 2 and end is None:
            raise ValueError("End prompt not found")
        paths = (
            [self.snapshots.normalize_path(path) for path in args[separator + 1 :]]
            if separator < len(args)
            else None
        )
        return turn.id, "code", end.id if end is not None else None, paths

    def select_model(
        self,
        choice: str,
        effort: str | None = None,
        *,
        thinking: bool | None = None,
        context_window: int | None = None,
    ) -> str:
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
        if context_window is not None and context_window < 0:
            raise ValueError("Context window must be nonnegative; use 0 for default")
        selected_window = (
            self.budget.context_window if self.context_window_explicit else None
        )
        if context_window is not None:
            selected_window = context_window or None
        config = ProviderConfig(
            provider=provider_name,
            model=model,
            base_url=base_url,
            thinking=self.provider_config.thinking if thinking is None else thinking,
            reasoning_effort=reasoning_effort,
            context_window=selected_window,
        )
        provider = build_provider(config)
        self._install_provider(config, provider, context_window=context_window)
        return f"Model: {provider_name}/{provider.model} · {config.reasoning_effort}"

    def _install_provider(
        self,
        config: ProviderConfig,
        provider: Provider,
        *,
        context_window: int | None = None,
    ) -> None:
        """先校验窗口，再一起更新主任务、压缩器和子任务。"""
        window = get_model_context_window(provider.model) or getattr(
            provider, "context_window", None
        )
        explicit = (
            self.context_window_explicit
            if context_window is None
            else context_window > 0
        )
        budget = self.budget
        if context_window is not None and context_window > 0:
            budget = Budget(context_window=context_window)
        elif not explicit and isinstance(window, int) and window > 0:
            budget = Budget(context_window=window)
        if (
            explicit
            and isinstance(window, int)
            and window > 0
            and budget.context_window > window
        ):
            raise ValueError(
                f"Configured context window {budget.context_window:,} "
                f"exceeds {provider.model} maximum {window:,}"
            )
        self.context_window_explicit = explicit
        self.provider_config, self.provider, self.budget = config, provider, budget
        self.compactor = Compactor(
            provider,
            config=self.compactor.config,
            extra_context=self.compactor.extra_context,
        )
        if isinstance(self.automatic_approver, AutoReviewer):
            self.automatic_approver.provider = provider
        for tool in self.base_tools:
            if isinstance(tool, DelegateTool):
                tool.runner.provider = provider
                tool.runner.budget = budget

    def expand(self, message: str) -> str:
        additions: list[str] = []
        for name in re.findall(r"(?<!\S)\$([A-Za-z0-9_.-]+)", message):
            skill = next((s for s in self.skills if s.name == name), None)
            if skill is None:
                raise ValueError(f"Unknown skill: {name}")
            loader = next(
                tool for tool in self.base_tools if isinstance(tool, LoadSkillTool)
            )
            additions.append(loader.run({"name": name}).content)
        message = re.sub(r"(?<!\S)\$[A-Za-z0-9_.-]+", "", message)
        for path_text in re.findall(r"(?<!\S)@(\S+)", message):
            path_text = path_text.rstrip(".,;:)")
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

    def _select_approver(
        self,
        approve: Callable[[ToolCallBlock], Awaitable[ApprovalResult]] | None,
        approver: Approver | None = None,
    ) -> Approver:
        """应用配置决定审批方式，宿主只提供人工交互回调。"""
        if approver is not None:
            return approver
        if self.automatic_approver is not None:
            return self.automatic_approver

        async def manual(request: ApprovalRequest) -> ApprovalResult:
            if approve is None:
                return ApprovalResult(False, "no approval handler")
            return await approve(request.call)

        return manual

    def request_context(
        self, entries: Sequence[LogEntry] | None = None
    ) -> tuple[str, ...]:
        """返回下一次模型请求使用的上下文，包含压缩后恢复的技能。"""
        parts = list(self.context())
        entries = self.log.branch() if entries is None else entries
        visible = project(entries)
        for tool in self.base_tools:
            if isinstance(tool, LoadSkillTool):
                parts.extend(restore_skill_context(tool.registry, entries, visible))
        return tuple(parts)

    async def run_task(
        self,
        text: str,
        on_event: Callable[[LoopEvent], None],
        approve: Callable[[ToolCallBlock], Awaitable[ApprovalResult]] | None = None,
        *,
        approver: Approver | None = None,
    ) -> RunResult:
        if self.running:
            raise RuntimeError("A task is already running")
        if isinstance(self.provider, UnavailableProvider):
            raise RuntimeError(self.provider.reason)  # noqa: TRY004 - authentication state
        text = self.expand(text)
        self.recovery.prepare_run(self.log)
        self.running = True
        self.cancel = self.cancel or CancellationToken()

        selected_approver = self._select_approver(approve, approver)
        runners = {
            tool.runner for tool in self.base_tools if isinstance(tool, DelegateTool)
        }
        previous_approvers = {runner: runner.approve for runner in runners}
        for runner in runners:
            runner.approve = selected_approver
        try:

            async def refresh_tools() -> Sequence[Tool]:
                if self.mcp is not None:
                    await self.mcp.refresh()
                return filter_tools(self.tools, self.policy.mode)

            result = await run(
                self.log,
                text,
                provider=self.provider,
                tools=filter_tools(self.tools, self.policy.mode),
                budget=self.budget,
                policy=self.policy,
                approve=selected_approver,
                context=self.context(),
                lifecycle=self.recovery,
                max_steps=self.max_steps,
                compact=self.compactor,
                window=self.window,
                steering=lambda: (
                    self.expand(self.steering.popleft()) if self.steering else None
                ),
                cancel=self.cancel,
                on_event=on_event,
                refresh_tools=refresh_tools,
                context_source=self.request_context,
            )
            self.last_usage = result.usage or {}
            return result
        finally:
            for runner, previous in previous_approvers.items():
                runner.approve = previous
            self.cancel = None
            self.running = False

    async def execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        approve: Callable[[ToolCallBlock], Awaitable[ApprovalResult]],
        on_event: Callable[[LoopEvent], None] | None = None,
    ) -> ToolResult:
        """直接工具调用共用权限、取消、会话事实和恢复检查点。"""
        if self.running:
            raise RuntimeError("A task is already running")
        self.recovery.prepare_run(self.log)
        call = ToolCallBlock(id=uuid4().hex[:12], name=name, args=args)
        self.running = True
        self.cancel = self.cancel or CancellationToken()
        try:
            with (
                self.log.run_lock(),
                self.recovery.record(self.log),
            ):
                text = (
                    f"!{args.get('command', '')}"
                    if name in {"bash", "powershell"}
                    else f"Run tool {name}"
                )
                user = self.log.append("user", {"text": text})
                self.recovery.started(self.log, user)
                messages = build(
                    project(self.log.branch()),
                    tools=(),
                    budget=self.budget,
                    context=self.context(),
                ).messages
                self.log.append(
                    "assistant",
                    {
                        "blocks": [call.model_dump(mode="json")],
                        "model": self.provider.model,
                        "provider": self.provider_config.provider,
                    },
                )
                execution = await execute_tool(
                    call,
                    index_tools(filter_tools(self.tools, self.policy.mode)),
                    policy=self.policy,
                    approve=self._select_approver(approve),
                    context=self.context(),
                    messages=messages,
                    cancel=self.cancel,
                    on_event=on_event,
                )
                result = execution.result
                self.log.append(
                    "tool_result",
                    {
                        "call_id": call.id,
                        "content": result.content,
                        "status": "error" if result.is_error else "ok",
                        "artifact": {"tool_metadata": cast(JsonValue, result.metadata)}
                        if result.metadata is not None
                        else None,
                    },
                )
                if on_event is not None:
                    on_event(
                        ToolEndEvent(
                            call.id, result.content, result.is_error, result.metadata
                        )
                    )
                return result
        finally:
            self.cancel = None
            self.running = False

    async def side_question(
        self,
        question: str,
        on_text: Callable[[str], None],
    ) -> str:
        """用独立 provider 回答上下文副本，不执行工具或写入会话。"""
        if not question.strip():
            raise ValueError("Side question must not be empty")
        provider = build_provider(self.provider_config)
        context = self.context()
        instructions = (
            "Answer this side question directly from the conversation so far. "
            "Do not use tools or take actions.\n\n"
        )
        conversation = [
            *project(self.log.branch()),
            UserMessage(text=instructions + question),
        ]
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
            if not arg:
                return CommandResult(picker="sessions")
            self._resume(arg)
            return CommandResult(
                (f"Session: {self.log.session_id}",),
                changed_session=True,
                draft=self.log.navigation_draft or "",
            )
        if command in {"/undo", "/redo"}:
            if arg:
                raise ValueError(f"Usage: {command}")
            if self.running:
                raise RuntimeError("Wait for the active task to finish before recovery")
            before = self.log.head_id
            message = self.recovery.step(
                self.log, "undo" if command == "/undo" else "redo"
            )
            changed = before != self.log.head_id
            return CommandResult(
                (message,),
                changed_session=changed,
                draft=(self.log.navigation_draft or "") if changed else None,
            )
        if command in {"/rewind", "/revert"}:
            if not arg:
                return CommandResult(picker=command[1:])
            if self.running:
                raise RuntimeError("Wait for the active task to finish before recovery")
            user_id, scope, end_id, paths = self.recovery_arguments(line)
            message = self.recovery.restore(
                self.log,
                user_id,
                scope=scope,
                kind="rewind" if command == "/rewind" else "revert",
                end_id=end_id,
                paths=paths,
            )
            return CommandResult(
                (message,), changed_session=True, draft=self.log.navigation_draft or ""
            )
        if command == "/tree":
            if not arg:
                return CommandResult(picker="tree")
            if self.running:
                raise RuntimeError(
                    "Wait for the active task to finish before navigating history."
                )
            self.log.switch_branch(arg)
            if not any(e.type == "system" for e in self.log.branch()):
                self.log.append("system", {"text": self.system_prompt})
            self.last_usage = {}
            return CommandResult(
                (f"History position: {arg}",),
                changed_session=True,
                draft=self.log.navigation_draft or "",
            )
        if command in {"/clone", "/fork"}:
            if command == "/fork" and not arg:
                return CommandResult(picker="fork")
            if self.running:
                raise RuntimeError(
                    "Wait for the active task to finish before copying history."
                )
            if command == "/clone" and arg:
                raise ValueError("Usage: /clone")
            if command == "/clone" and not any(
                e.type in {"user", "assistant"} for e in self.log.branch()
            ):
                return CommandResult(("Nothing to clone yet",))
            draft = ""
            if command == "/fork":
                turn = self.resolve_turn(arg)
                if turn is None:
                    raise ValueError("No matching user turn")
                draft = str(turn.data.get("text", ""))
                copied = self.log.copy_branch(
                    self.sessions_dir,
                    before_entry_id=turn.id,
                    project_root=self.workspace,
                    deferred=True,
                )
            else:
                copied = self.log.copy_branch(
                    self.sessions_dir, project_root=self.workspace, deferred=True
                )
            self.log = copied
            if not any(e.type == "system" for e in self.log.branch()):
                self.log.append("system", {"text": self.system_prompt})
            self.policy.grants.clear()
            self.last_usage = {}
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
            if not arg:
                return CommandResult(picker="thinking")
            levels = dict(self.choices("thinking"))
            if arg not in levels:
                raise ValueError(
                    f"Unknown thinking level: {arg}. Available: {', '.join(levels)}"
                )
            self.select_model(self.provider_config.model, levels[arg])
            return CommandResult((f"Thinking level: {arg}",))
        if command == "/context":
            return CommandResult(context_usage=await inspect_context_usage(self))
        if command == "/context-window":
            if not arg:
                return CommandResult((f"Window: {self.budget.context_window:,}",))
            available = get_model_context_window(self.provider.model) or getattr(
                self.provider, "context_window", None
            )
            size = (available or 200_000) if arg == "default" else _size(arg)
            self._install_provider(
                replace(
                    self.provider_config,
                    context_window=None if arg == "default" else size,
                ),
                self.provider,
                context_window=0 if arg == "default" else size,
            )
            return CommandResult((f"Window: {size:,}",))
        if command == "/compact":
            with self.log.run_lock():
                ok = await self.compactor(
                    self.log, self.budget, instructions=arg or None
                )
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
            elif arg in {"pause", "resume"}:
                return CommandResult((f"Goal {arg} is not supported.",))
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
            raise ValueError("Usage: /tool [list]")
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
            if (
                arg == self.provider_config.provider
                and arg not in self.settings.configured_providers(self.auth.store)
            ):
                self._install_provider(
                    self.provider_config,
                    UnavailableProvider(
                        self.provider.model,
                        self.provider.transport,
                        f"Missing {arg} login; use /login to connect.",
                    ),
                )
            return CommandResult((message,))
        return CommandResult((f"Unknown command: {command}. Type /help.",))
