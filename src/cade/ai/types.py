"""AI 层类型定义：核心类型与 provider 配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import orjson

type ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
type ReasoningSummary = Literal["auto", "concise", "detailed"]
type ToolArguments = dict[str, object]


@dataclass(frozen=True)
class ProviderConfig:
    """标准化 provider 构造参数。

    所有 provider 共享此构造契约，factory 无需感知 provider 专有字段。
    专有配置通过 extra 传递，各 provider 在 __init__ 中自行提取。
    """

    api_key: str
    model: str
    base_url: str = ""
    context_window: int | None = None
    thinking: bool = True
    reasoning_effort: str | None = None
    response_format: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Cost:
    """LLM 调用成本（美元/百万 token）。"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass(frozen=True)
class Model:
    """模型元数据。"""

    id: str
    name: str
    api: str
    provider: str
    context_window: int
    max_tokens: int
    base_url: str = ""
    reasoning: bool = False
    cost: Cost = field(default_factory=Cost)
    thinking_level_map: dict[str, str | None] | None = None
    reasoning_efforts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")


@dataclass(frozen=True)
class StreamOptions:
    """单次 provider 请求的可选覆盖参数。

    运行时与认证字段不进入请求快照；保留它们是为了让快照逻辑有明确的排除对象。
    """

    temperature: float | None = None
    max_tokens: int | None = None
    signal: Any | None = None
    api_key: str | None = None
    session_id: str | None = None
    reasoning_summary: ReasoningSummary | None = None
    headers: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None
    on_payload: Any | None = None
    on_response: Any | None = None
    tool_choice: str | dict[str, Any] | None = None
    top_p: float | None = None


@dataclass(frozen=True)
class ToolDefinition:
    """LLM 可见的工具 schema。"""

    name: str
    description: str
    parameters: dict[str, Any]
    builtin: dict[str, Any] | None = None


# ── 上下文序列化 ──


def dump_context(
    system_prompt: str | None,
    messages: list[dict[str, Any]],
) -> str:
    obj: dict[str, Any] = {"messages": messages}
    if system_prompt:
        obj["system_prompt"] = system_prompt
    return orjson.dumps(obj, default=str).decode()


def load_context(data: str) -> tuple[str | None, list[dict[str, Any]]]:
    obj = orjson.loads(data.encode())
    messages = obj.get("messages", [])
    system_prompt: str | None = obj.get("system_prompt")
    return system_prompt, messages
