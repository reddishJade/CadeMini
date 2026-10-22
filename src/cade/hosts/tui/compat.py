"""Cade TUI 与 Cade 宿主之间的轻量类型适配。

这里只保留 TUI 真正使用的 Cade 数据形状，避免把 Cade 的运行时整体搬入
Cade。事件、会话视图和授权结果在这里完成一次性转换。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .bridge import CadeInteractionApp
    from .command_output import TerminalCommandOutput

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from cade.harness.events import ProviderRetryEvent

type ToolInput = dict[str, Any]
type ApprovalScope = Literal["once", "session", "permanent"]


class TerminalRenderIntent(BaseModel):
    """将工具结果呈现为一次终端执行。"""

    kind: Literal["terminal"] = "terminal"
    command: str
    cwd: str
    model_config = ConfigDict(frozen=True, extra="forbid")


class DiffRenderIntent(BaseModel):
    """将工具结果呈现为结构化文件差异。"""

    kind: Literal["diff"] = "diff"
    patch: str
    files: tuple[str, ...] = ()
    first_changed_line: int | None = None
    model_config = ConfigDict(frozen=True, extra="forbid")


class LocationRenderIntent(BaseModel):
    """将工具结果指向工作区位置。"""

    kind: Literal["location"] = "location"
    path: str
    line_start: int | None = None
    line_end: int | None = None
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubagentRenderIntent(BaseModel):
    """将工具结果聚合为子代理批次。"""

    kind: Literal["subagent"] = "subagent"
    batch_id: str
    run_ids: tuple[str, ...] = ()
    model_config = ConfigDict(frozen=True, extra="forbid")


type ToolRenderIntent = (
    TerminalRenderIntent
    | DiffRenderIntent
    | LocationRenderIntent
    | SubagentRenderIntent
)

_RENDER_INTENT_ADAPTER = TypeAdapter(ToolRenderIntent)


def parse_tool_render_intent(value: object) -> ToolRenderIntent | None:
    """从持久化记录恢复工具呈现意图；非法值按缺失处理。"""
    if value is None:
        return None
    try:
        return _RENDER_INTENT_ADAPTER.validate_python(value)
    except ValidationError:
        return None


class ToolSpec(Protocol):
    """授权预览需要的最小工具元数据。"""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...


@dataclass(frozen=True)
class UserMessage:
    """TUI 提交给宿主的用户消息。"""

    content: str
    source: str = "user"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: ToolInput = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str = ""
    status: str = "ok"
    permission_notice: str | None = None
    render_intent: ToolRenderIntent | None = None


@dataclass(frozen=True)
class AgentHarnessResult:
    answer: str = ""
    stop_reason: str = "end_turn"
    steps: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ResponseMetricsData:
    ttft_seconds: float | None = None
    tokens_per_second: float | None = None
    completed: bool = False


@dataclass(frozen=True)
class TextDeltaStructuredEvent:
    data: str
    step: int = 0
    type: Literal["text_delta"] = "text_delta"


@dataclass(frozen=True)
class ReasoningDeltaStructuredEvent:
    data: str
    step: int = 0
    type: Literal["reasoning_delta"] = "reasoning_delta"


@dataclass(frozen=True)
class UsageUpdateStructuredEvent:
    input_tokens: int
    output_tokens: int
    type: Literal["usage_update"] = "usage_update"


@dataclass(frozen=True)
class ResponseMetricsStructuredEvent:
    data: ResponseMetricsData
    step: int = 0
    type: Literal["response_metrics"] = "response_metrics"


@dataclass(frozen=True)
class ToolUseStructuredEvent:
    data: ToolCall
    step: int = 0
    type: Literal["tool_use"] = "tool_use"


@dataclass(frozen=True)
class ToolUpdateStructuredEvent:
    data: Any
    step: int = 0
    type: Literal["tool_update"] = "tool_update"


@dataclass(frozen=True)
class ToolResultStructuredEvent:
    data: ToolResultBlock
    step: int = 0
    type: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True)
class FinalStructuredEvent:
    data: AgentHarnessResult
    step: int = 0
    type: Literal["final"] = "final"


type AgentHarnessEvent = (
    TextDeltaStructuredEvent
    | ProviderRetryEvent
    | ReasoningDeltaStructuredEvent
    | UsageUpdateStructuredEvent
    | ResponseMetricsStructuredEvent
    | ToolUseStructuredEvent
    | ToolUpdateStructuredEvent
    | ToolResultStructuredEvent
    | FinalStructuredEvent
)


class BusyMessageMode(StrEnum):
    """忙碌时新消息的处理策略。"""

    STEER = "steer"
    FOLLOW_UP = "followup"
    INTERRUPT = "interrupt"


class SubmitStatus(StrEnum):
    """输入被调度后的可观测结果。"""

    STEER_ACCEPTED = "steer_accepted"
    FOLLOW_UP_QUEUED = "followup_queued"
    INJECT_QUEUED = "inject_queued"
    INTERRUPT_REQUESTED = "interrupt_requested"


@dataclass(frozen=True)
class SubmitOutcome:
    status: SubmitStatus
    run_id: str | None = None
    wake_required: bool = False


@dataclass
class HITLResult:
    """授权菜单的选择结果。"""

    action: Literal["allow", "deny"]
    scope: ApprovalScope = "once"
    suggestion: str = ""


@dataclass(frozen=True)
class ApprovalRequest:
    """TUI 授权预览所需的请求形状。"""

    tool: ToolSpec
    action_input: ToolInput
    allowed_scopes: tuple[ApprovalScope, ...] = ("once", "session")
    reason: str = ""


def hitl_choices(scopes: tuple[ApprovalScope, ...]) -> tuple[str, ...]:
    labels = {
        "once": "Allow once",
        "session": "Allow for session",
        "permanent": "Always allow",
    }
    choices = [labels[scope] for scope in scopes]
    choices.append("Deny")
    return tuple(choices)


HITL_CHOICES = hitl_choices(("once", "session"))


def parse_hitl_choice(text: str) -> HITLResult | None:
    """把菜单文字还原为授权结果。"""
    if text == "Allow once":
        return HITLResult("allow", "once")
    if text == "Allow for session":
        return HITLResult("allow", "session")
    if text == "Always allow":
        return HITLResult("allow", "permanent")
    if text == "Deny":
        return HITLResult("deny", "once")
    return None


@dataclass
class InteractionState:
    """TUI 命令层共享的可变状态。"""

    mode: str = "build"
    busy_mode: BusyMessageMode = BusyMessageMode.FOLLOW_UP
    thinking: bool = True
    session_grant_store: Any = None
    draft_input: str | None = None


@dataclass
class CommandContext:
    """命令分发需要的终端上下文，不复制服务层的会话和权限状态。"""

    app: CadeInteractionApp
    output: TerminalCommandOutput
    state: InteractionState = field(default_factory=InteractionState)
    changed_session: bool = False


@dataclass(frozen=True)
class SessionEntry:
    """TUI 恢复历史时使用的会话视图。"""

    id: str
    type: str
    content: Any
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionInfoView:
    """会话列表的展示投影。"""

    id: str
    title: str
    path: Path
    created_at: str = ""
    updated_at: str = ""
    project_path: str = ""


@dataclass(frozen=True)
class ContextSummary:
    """状态栏上下文用量与模型信息。"""

    snapshot: Any = None
    model_name: str = ""
    instruction_files: list[str] = field(default_factory=list)
    skill_count: int = 0
    skill_tokens: int = 0
    usage_stats: str = ""
    context_usage: str = ""


def final_stop_reason(data: AgentHarnessResult) -> str | None:
    """把终止原因转换为 TUI 的停止提示。"""
    if data.stop_reason in {"step_limit", "max_steps"}:
        return "[stopped] step limit reached"
    if data.stop_reason in {"cancelled", "aborted"}:
        return "[stopped] cancelled"
    if data.stop_reason in {"error", "provider_error"}:
        return f"[stopped] {data.error or 'provider error'}"
    return None


@dataclass(frozen=True)
class ShellAnalysis:
    """shell 命令的轻量分析结果。"""

    primary_command: str
    classification_available: bool = True
    parse_error: str = ""
    unresolved_effects: tuple[Any, ...] = ()
    resolved_paths: tuple[Any, ...] = ()


_READ_ONLY_COMMANDS = frozenset(
    {
        "cat",
        "dir",
        "echo",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "pwd",
        "rg",
        "sed",
        "tail",
        "type",
        "where",
        "which",
    }
)


def analyze_shell_command(command: str) -> ShellAnalysis:
    """提取命令主程序，供 TUI 判断是否为探索型调用。"""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    return ShellAnalysis(parts[0].lower() if parts else "")


def command_grant_pattern(command: str) -> str:
    """为授权提示生成稳定、可读的 shell 规则。"""
    analysis = analyze_shell_command(command)
    return f"{analysis.primary_command} *" if analysis.primary_command else "*"
