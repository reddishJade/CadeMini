"""Cade Mini 核心类型：消息、日志条目与错误。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

type EventType = Literal[
    "meta",
    "system",
    "user",
    "assistant",
    "tool_result",
    "compaction",
    "window",
    "request",
    "run",
    "attempt",
    "branch",
]


class TextBlock(BaseModel):
    """助手消息中的文本块。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["text"] = "text"
    text: str


class ToolCallBlock(BaseModel):
    """助手消息中的工具调用块。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    args: dict[str, JsonValue] = Field(default_factory=dict)


type ContentBlock = Annotated[
    TextBlock | ToolCallBlock,
    Field(discriminator="type"),
]


class SystemMessage(BaseModel):
    """系统提示词，投影时总是排在最前。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["system"] = "system"
    text: str


class UserMessage(BaseModel):
    """用户或运行时输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["user"] = "user"
    text: str
    source: Literal["user", "runtime"] = "user"


class SummaryMessage(BaseModel):
    """压缩摘要或换窗交接，线上渲染为 user 角色。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["summary"] = "summary"
    text: str
    source_id: str
    kind: Literal["compaction", "window"] = "compaction"


class AssistantMessage(BaseModel):
    """模型输出；工具调用只记录在这里。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["assistant"] = "assistant"
    blocks: tuple[ContentBlock, ...] = ()
    provider_state: dict[str, JsonValue] | None = None


class ToolResultMessage(BaseModel):
    """工具执行结果，按 call_id 与助手调用配对。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["tool_result"] = "tool_result"
    call_id: str
    content: str
    is_error: bool = False


type Message = (
    SystemMessage | UserMessage | SummaryMessage | AssistantMessage | ToolResultMessage
)


class LogEntry(BaseModel):
    """会话日志中的一行；data 的具体形状由 type 决定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    v: Literal[1, 2, 3] = 3
    id: str
    parent: str | None = None
    ts: str
    type: EventType
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_version(self) -> LogEntry:
        """旧版本不得借用新版本才定义的导航事件。"""
        if self.type == "branch" and self.v < 3:
            raise ValueError("branch events require session v3")
        return self


class MiniError(RuntimeError):
    """Cade Mini 的可恢复领域错误。"""


class LogError(MiniError):
    """会话日志无法安全读写。"""


class ProjectionError(MiniError):
    """日志事实无法投影为合法消息序列。"""
