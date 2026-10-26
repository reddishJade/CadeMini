"""Cade 核心类型：消息、日志条目与错误。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

type EventType = Literal[
    "meta",
    "system",
    "user",
    "assistant",
    "tool_result",
    "tool_execution",
    "compaction",
    "window",
    "request",
    "run",
    "attempt",
    "branch",
    "checkpoint",
    "restore",
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


class ImageAttachment(BaseModel):
    """工具返回的内联图片；日志保留原始数据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    data: str
    mimeType: str


class ToolResultMessage(BaseModel):
    """工具执行结果，按 call_id 与助手调用配对。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["tool_result"] = "tool_result"
    call_id: str
    content: str
    is_error: bool = False
    images: tuple[ImageAttachment, ...] = ()


type Message = (
    SystemMessage | UserMessage | SummaryMessage | AssistantMessage | ToolResultMessage
)


class LogEntry(BaseModel):
    """会话日志中的一行；data 的具体形状由 type 决定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    parent: str | None = None
    ts: str
    type: EventType
    data: dict[str, JsonValue]


class CadeError(RuntimeError):
    """Cade 的可恢复领域错误。"""


class LogError(CadeError):
    """会话日志无法安全读写。"""


class ProjectionError(CadeError):
    """日志事实无法投影为合法消息序列。"""
