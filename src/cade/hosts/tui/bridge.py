"""Cade TUI 与 Cade 交互服务之间的适配器。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from cade.ai.models import get_model_context_window, get_models
from cade.app.session import ApplicationSession
from cade.coding.security.sensitive import is_sensitive_reason
from cade.harness.cancellation import CancellationToken
from cade.harness.events import (
    LoopEvent,
    ProviderRetryEvent,
    ReasoningDeltaEvent,
    ResponseMetricsEvent,
    RunResult,
    TextDeltaEvent,
    ToolEndEvent,
    ToolStartEvent,
    ToolUpdateEvent,
    UsageEvent,
)
from cade.harness.permissions import ApprovalResult
from cade.harness.session.log import SessionLog, list_sessions
from cade.harness.session.types import LogEntry, ToolCallBlock

from .compat import (
    AgentHarnessResult,
    ApprovalRequest,
    BusyMessageMode,
    DiffRenderIntent,
    FinalStructuredEvent,
    ReasoningDeltaStructuredEvent,
    ResponseMetricsData,
    ResponseMetricsStructuredEvent,
    SessionEntry,
    SessionInfoView,
    SubmitOutcome,
    SubmitStatus,
    TextDeltaStructuredEvent,
    ToolCall,
    ToolResultBlock,
    ToolResultStructuredEvent,
    ToolUpdateData,
    ToolUpdateStructuredEvent,
    ToolUseStructuredEvent,
    UsageUpdateStructuredEvent,
)

_SENTINEL = object()


def _edit_intent(metadata: object, path: str) -> DiffRenderIntent | None:
    if not isinstance(metadata, dict) or not isinstance(metadata.get("patch"), str):
        return None
    line = metadata.get("firstChangedLine")
    return DiffRenderIntent(
        patch=metadata["patch"],
        files=(path,) if path else (),
        first_changed_line=line if isinstance(line, int) else None,
    )


def _tool_text(entry: LogEntry) -> str:
    """从日志条目提取可读文本。"""
    data = entry.data
    direct = data.get("text")
    if isinstance(direct, str):
        return direct
    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _session_project(path: Path) -> str:
    """读取会话 meta 中的项目路径；损坏会话返回空字符串。"""
    try:
        log = SessionLog.open(path)
        meta = next((entry for entry in log.branch() if entry.type == "meta"), None)
    except (OSError, ValueError):
        return ""
    if meta is None:
        return ""
    project = meta.data.get("project")
    return project if isinstance(project, str) else ""


class CadeSessionStore:
    """把 Cade 的线性会话日志投影成 TUI 需要的会话存储接口。"""

    def __init__(self, service: ApplicationSession) -> None:
        self._service = service

    @property
    def session_id(self) -> str:
        return self._service.log.session_id

    @property
    def current_path(self) -> Path:
        return self._service.log.path

    @property
    def project_root(self) -> Path:
        return self._service.workspace

    def append(self, record_type: str, content: dict[str, Any]) -> str:
        """TUI 派生事件不写入模型事实日志；返回稳定占位 ID。"""
        del record_type, content
        return uuid4().hex[:12]

    def build_branch(self) -> list[SessionEntry]:
        return self._project(self._service.log.branch())

    def _project(self, entries: list[LogEntry]) -> list[SessionEntry]:
        result: list[SessionEntry] = []
        paths: dict[str, str] = {}
        for entry in entries:
            if entry.type == "user":
                text = entry.data.get("text")
                if isinstance(text, str) and text:
                    result.append(
                        SessionEntry(entry.id, "user", str(text), dict(entry.data))
                    )
            elif entry.type == "assistant":
                blocks = entry.data.get("blocks")
                if not isinstance(blocks, list):
                    continue
                text = _tool_text(entry)
                if text:
                    result.append(
                        SessionEntry(entry.id, "assistant", text, dict(entry.data))
                    )
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "tool_call":
                        continue
                    call_id = block.get("id")
                    name = block.get("name")
                    args = block.get("args")
                    if isinstance(call_id, str) and isinstance(name, str):
                        paths[call_id] = (
                            str(args.get("path", "")) if isinstance(args, dict) else ""
                        )
                        result.append(
                            SessionEntry(
                                entry.id,
                                "event",
                                {
                                    "type": "tool_use",
                                    "data": {
                                        "id": call_id,
                                        "name": name,
                                        "input": args if isinstance(args, dict) else {},
                                    },
                                },
                            )
                        )
            elif entry.type == "tool_result":
                call_id = entry.data.get("call_id")
                content = entry.data.get("content")
                status = entry.data.get("status")
                if isinstance(call_id, str):
                    artifact = entry.data.get("artifact")
                    intent = _edit_intent(
                        artifact.get("tool_metadata")
                        if isinstance(artifact, dict)
                        else None,
                        paths.get(call_id, ""),
                    )
                    authorization = (
                        artifact.get("authorization")
                        if isinstance(artifact, dict)
                        else None
                    )
                    notice = (
                        authorization.get("reason")
                        if isinstance(authorization, dict)
                        and authorization.get("decision") == "allow"
                        else None
                    )
                    result.append(
                        SessionEntry(
                            entry.id,
                            "event",
                            {
                                "type": "tool_result",
                                "data": {
                                    "tool_use_id": call_id,
                                    "content": content
                                    if isinstance(content, str)
                                    else "",
                                    "status": status
                                    if isinstance(status, str)
                                    else "ok",
                                    "permission_notice": notice
                                    if isinstance(notice, str)
                                    else None,
                                    "render_intent": intent.model_dump()
                                    if intent is not None
                                    else None,
                                },
                            },
                        )
                    )
            elif entry.type == "compaction":
                result.append(
                    SessionEntry(
                        entry.id, "system", "Context compacted", dict(entry.data)
                    )
                )
        return result

    def clear(self) -> None:
        self._service._new()

    def resume(self, target: str | Path) -> None:
        text = str(target)
        if text.endswith(".jsonl"):
            self._service.log = SessionLog.open(Path(text))
        else:
            self._service._resume(text)

    def list_infos(self, limit: int | None = None) -> list[SessionInfoView]:
        infos = list_sessions(self._service.sessions_dir)
        views = [
            SessionInfoView(
                id=info.id,
                title=info.title,
                path=info.path,
                created_at=info.created_at,
                updated_at=info.updated_at,
                project_path=_session_project(info.path),
            )
            for info in infos
        ]
        return views[:limit] if limit is not None else views

    def find_by_id(self, session_id: str) -> SessionInfoView | None:
        return next((item for item in self.list_infos() if item.id == session_id), None)

    def find_latest_for_project(self, project_root: Path) -> SessionInfoView | None:
        root = project_root.resolve()
        for item in self.list_infos():
            if item.project_path and Path(item.project_path).resolve() == root:
                return item
        return self.list_infos()[0] if self.list_infos() else None

    def get_forkable_user_messages(self) -> list[SessionEntry]:
        return [
            entry
            for entry in self._project(self._service.log.entries())
            if entry.type == "user"
        ]


class _RunToken:
    """在启动后台线程前准备取消令牌，保留发送后立即中断的请求。"""

    def __init__(self, service: ApplicationSession) -> None:
        self._service = service

    def reset(self) -> None:
        if not self._service.running:
            self._service.cancel = CancellationToken()

    def cancel(self, reason: str = "cancelled") -> None:
        if self._service.cancel is not None:
            self._service.cancel.cancel(reason)


class CadeAgent:
    """TUI 看到的 agent 外观；运行由 ApplicationSession 负责。"""

    approval_policy = "on-request"
    approvals_reviewer = "user"

    @property
    def current_mode(self) -> str:
        return self._service.policy.mode

    def __init__(self, service: ApplicationSession) -> None:
        self._service = service
        self.user_approval_callback: Any = None
        self.session_id = service.log.session_id
        self.cancellation_token = _RunToken(service)

    @property
    def provider(self) -> Any:
        return self._service.provider

    @property
    def permission_policy(self) -> Any:
        return self._service.policy

    @property
    def restricted_dirs(self) -> tuple[str, ...]:
        return ()

    def submit_busy_message(
        self,
        message: Any,
        mode: BusyMessageMode | None = None,
        display_text: str | None = None,
    ) -> SubmitOutcome:
        del display_text
        text = message.content if hasattr(message, "content") else str(message)
        selected = mode or BusyMessageMode.FOLLOW_UP
        if selected is BusyMessageMode.STEER:
            self._service.steering.append(str(text))
            return SubmitOutcome(SubmitStatus.STEER_ACCEPTED)
        if selected is BusyMessageMode.INTERRUPT:
            if self._service.cancel is not None:
                self._service.cancel.cancel("interrupted by user")
            self._service.pending.append(str(text))
            return SubmitOutcome(SubmitStatus.INTERRUPT_REQUESTED)
        self._service.pending.append(str(text))
        return SubmitOutcome(SubmitStatus.FOLLOW_UP_QUEUED)

    def interrupt(self, reason: str) -> bool:
        if self._service.cancel is None:
            return False
        self._service.cancel.cancel(reason)
        return True

    def has_pending_input(self) -> bool:
        return bool(self._service.pending or self._service.steering)

    async def approve(self, call: ToolCallBlock) -> ApprovalResult:
        check = self._service.policy.check(call)
        if check.decision == "deny":
            return ApprovalResult(False, check.reason)
        sensitive = is_sensitive_reason(check.reason)
        callback = self.user_approval_callback
        if callback is None:
            return ApprovalResult(False, "no approval handler")
        tool = cast(
            Any,
            next(
                (item for item in self._service.tools if item.name == call.name),
                SimpleNamespace(name=call.name, description=""),
            ),
        )
        request = ApprovalRequest(
            tool=tool,
            action_input=dict(call.args),
            allowed_scopes=("once",) if sensitive else ("once", "session"),
            reason=check.reason or "Tool requires approval",
        )
        result = callback(request)
        if asyncio.iscoroutine(result):
            result = await result
        if result.action != "allow":
            return ApprovalResult(False, result.suggestion.strip() or "denied by user")
        if result.scope == "session" and not sensitive:
            self._service.policy.grants.add(call)
        return ApprovalResult(True, "approved by user")


class CadeInteractionApp:
    """Cade TUI 协议在 Cade 上的实现。"""

    def __init__(self, service: ApplicationSession) -> None:
        self.service = service
        self.agent = CadeAgent(service)
        self.session_store = CadeSessionStore(service)
        self._pending_skill: str | None = None

    @property
    def registry(self) -> tuple[Any, ...]:
        return self.service.tools

    def instruction_paths(self) -> tuple[Path, ...]:
        return (self.service.workspace / "AGENTS.md",)

    def restore_session(self) -> None:
        self.agent.session_id = self.session_store.session_id
        self.agent.cancellation_token = _RunToken(self.service)

    def get_model_info(self) -> dict[str, str]:
        config = self.service.provider_config
        model = self.service.provider.model
        return {
            "model": model,
            "provider": config.provider,
            "transport": str(getattr(self.service.provider, "transport", "")),
            "base_url": config.base_url,
            "reasoning_effort": config.reasoning_effort or "",
            "thinking": "on" if config.thinking else "off",
            "context_window": str(self.service.budget.context_window),
            "model_context_window": str(get_model_context_window(model) or ""),
        }

    def available_models(self) -> list[tuple[str, str]]:
        providers = self.service.settings.configured_providers(self.service.auth.store)
        return [
            (provider, model.id)
            for provider in providers
            for model in get_models(provider)
        ]

    def ask_stream(
        self,
        text: str | None,
        *,
        mode: str | None = None,
        display_question: str | None = None,
    ) -> Iterator[Any]:
        del display_question
        if mode is not None:
            self.service.policy.set_mode("build" if mode == "act" else mode)
        if not text:
            queued = list(self.service.pending)
            self.service.pending.clear()
            if not queued:
                yield FinalStructuredEvent(AgentHarnessResult())
                return
            text = "\n\n".join(queued)
        pending_skill = getattr(self, "_pending_skill", None)
        if pending_skill:
            text = f"${pending_skill} {text}"
            self._pending_skill = None
        events: Queue[Any] = Queue()
        paths: dict[str, str] = {}

        def emit(event: LoopEvent) -> None:
            if isinstance(event, ProviderRetryEvent):
                events.put(event)
            elif isinstance(event, TextDeltaEvent):
                events.put(TextDeltaStructuredEvent(event.text))
            elif isinstance(event, ReasoningDeltaEvent):
                events.put(ReasoningDeltaStructuredEvent(event.text))
            elif isinstance(event, ResponseMetricsEvent):
                events.put(
                    ResponseMetricsStructuredEvent(
                        ResponseMetricsData(
                            event.ttft_seconds,
                            event.tokens_per_second,
                            event.completed,
                        )
                    )
                )
            elif isinstance(event, UsageEvent):
                events.put(
                    UsageUpdateStructuredEvent(event.input_tokens, event.output_tokens)
                )
            elif isinstance(event, ToolStartEvent):
                paths[event.call_id] = str(event.args.get("path", ""))
                events.put(
                    ToolUseStructuredEvent(
                        ToolCall(event.call_id, event.name, dict(event.args))
                    )
                )
            elif isinstance(event, ToolEndEvent):
                events.put(
                    ToolResultStructuredEvent(
                        ToolResultBlock(
                            event.call_id,
                            event.content,
                            "error" if event.is_error else "ok",
                            permission_notice=event.authorization_reason or None,
                            render_intent=_edit_intent(
                                event.metadata, paths.pop(event.call_id, "")
                            ),
                        )
                    )
                )
            elif isinstance(event, ToolUpdateEvent):
                events.put(
                    ToolUpdateStructuredEvent(ToolUpdateData(event.name, event.content))
                )

        def worker() -> None:
            try:
                result = asyncio.run(
                    self.service.run_task(text, emit, self.agent.approve)
                )
                events.put(self._final_event(result))
            except Exception as exc:  # noqa: BLE001
                events.put(exc)
            finally:
                events.put(_SENTINEL)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = events.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def _final_event(self, result: RunResult) -> FinalStructuredEvent:
        return FinalStructuredEvent(
            AgentHarnessResult(
                answer=result.answer,
                stop_reason=result.termination,
                steps=result.steps,
                error=result.error,
            )
        )


def build_app(service: ApplicationSession) -> CadeInteractionApp:
    """由 CLI 使用的工厂，保持入口简洁。"""
    return CadeInteractionApp(service)
