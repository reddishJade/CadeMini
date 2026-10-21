"""运行事件与轮次结果；独立于终端展示。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cade.harness.session.types import (
    JsonValue,
)

type Termination = Literal[
    "completed",
    "max_tokens",
    "step_limit",
    "context_error",
    "provider_error",
    "aborted",
]


@dataclass(frozen=True)
class TextDeltaEvent:
    text: str


@dataclass(frozen=True)
class ToolStartEvent:
    call_id: str
    name: str
    args: dict[str, JsonValue]


@dataclass(frozen=True)
class ToolEndEvent:
    call_id: str
    content: str
    is_error: bool


@dataclass(frozen=True)
class ReasoningDeltaEvent:
    """推理摘要增量；只用于宿主展示，不进入 API 投影。"""

    text: str


@dataclass(frozen=True)
class UsageEvent:
    """一次 provider 调用的累计用量，供宿主实时更新状态栏。"""

    input_tokens: int
    output_tokens: int


type LoopEvent = (
    TextDeltaEvent | ToolStartEvent | ToolEndEvent | ReasoningDeltaEvent | UsageEvent
)


@dataclass(frozen=True)
class RunResult:
    answer: str
    steps: int
    termination: Termination
    error: str | None = None
    usage: dict[str, int] | None = None
