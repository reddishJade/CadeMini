"""纯请求构造：投影 + 当前上下文 + 工具 + 预算。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, cast

from cade.ai.types import StreamOptions, ToolDefinition
from cade.harness.session.types import (
    AssistantMessage,
    JsonValue,
    Message,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    UserMessage,
)

_SUMMARY_TAGS = {"compaction": "compaction-summary", "window": "window-handoff"}


class RequestTooLarge(RuntimeError):
    """准入后仍未落在输入预算内。"""


@dataclass(frozen=True)
class Budget:
    """模型窗口、输出预留与安全余量。"""

    context_window: int
    output_reserve: int = 16384
    headroom_tokens: int = 1024

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.output_reserve < 0:
            raise ValueError("output_reserve must be nonnegative")
        if self.headroom_tokens < 0:
            raise ValueError("headroom_tokens must be nonnegative")
        if self.input_budget <= 0:
            raise ValueError("output reserve and headroom leave no input budget")

    @property
    def input_budget(self) -> int:
        return self.context_window - self.output_reserve - self.headroom_tokens


@dataclass(frozen=True)
class BuildReport:
    """完整投影的准入结果；历史裁剪计数保留为零。"""

    estimated_tokens: int
    budget_tokens: int
    over_budget: bool
    dropped_tool_calls: tuple[str, ...] = ()
    dropped_turns: int = 0
    context_tokens: int = 0


@dataclass(frozen=True)
class Request:
    """一次 provider 调用的完整快照。"""

    messages: tuple[dict[str, Any], ...]
    tools: tuple[ToolDefinition, ...]
    options: StreamOptions | None
    report: BuildReport
    admitted: tuple[Message, ...]
    budget: Budget

    def as_record(self) -> dict[str, JsonValue]:
        """返回规范化请求输入，不保存认证参数或运行时对象。"""
        options: dict[str, Any] | None = None
        if self.options is not None:
            excluded = {"api_key", "headers", "signal", "on_payload", "on_response"}
            options = {
                field.name: getattr(self.options, field.name)
                for field in fields(self.options)
                if field.name not in excluded
            }
        record = {
            "messages": list(self.messages),
            "tools": [asdict(tool) for tool in self.tools],
            "options": options,
            "budget": asdict(self.budget),
            "report": asdict(self.report),
        }
        # JSON 往返隔离嵌套对象，并在调用 Provider 前拒绝不可序列化的输入。
        return cast(
            dict[str, JsonValue],
            json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False)),
        )

    def require_sendable(self) -> None:
        if self.report.over_budget:
            raise RequestTooLarge(
                f"request needs {self.report.estimated_tokens} tokens; "
                f"budget is {self.report.budget_tokens}"
            )

    @property
    def conversation_digest(self) -> str:
        return _digest({"messages": list(self.messages)})

    @property
    def tools_digest(self) -> str:
        return _digest({"tools": [asdict(tool) for tool in self.tools]})

    @property
    def digest(self) -> str:
        return _digest(
            {
                "messages": list(self.messages),
                "tools": [asdict(tool) for tool in self.tools],
            }
        )


def estimate(
    conversation: Sequence[Message],
    *,
    tools: Sequence[ToolDefinition] = (),
    context: Sequence[str] = (),
) -> int:
    """估算完整投影加工具的请求大小，用于决定是否先压缩。"""
    _system_text, messages = _prepare(conversation, context)
    return _estimate_messages(messages) + _estimate_tools(tools)


def build(
    conversation: Sequence[Message],
    *,
    tools: Sequence[ToolDefinition] = (),
    budget: Budget,
    options: StreamOptions | None = None,
    context: Sequence[str] = (),
) -> Request:
    """完整转换投影并检查预算；不裁剪消息，不执行 I/O。"""
    system_text, messages = _prepare(conversation, context)

    estimated = _estimate_messages(messages) + _estimate_tools(tools)

    return Request(
        messages=tuple(_to_wire(message) for message in messages),
        tools=tuple(tools),
        options=options,
        report=BuildReport(
            estimated_tokens=estimated,
            budget_tokens=budget.input_budget,
            over_budget=estimated > budget.input_budget,
            context_tokens=_tokens(system_text) if context else 0,
        ),
        admitted=tuple(messages),
        budget=budget,
    )


def _prepare(
    conversation: Sequence[Message],
    context: Sequence[str],
) -> tuple[str, list[Message]]:
    base_system = next(
        (
            message.text
            for message in conversation
            if isinstance(message, SystemMessage)
        ),
        "",
    )
    system_text = _join_system(base_system, context)
    messages: list[Message] = []
    if system_text:
        messages.append(SystemMessage(text=system_text))
    messages.extend(
        message for message in conversation if not isinstance(message, SystemMessage)
    )
    return system_text, messages


def _join_system(base: str, context: Sequence[str]) -> str:
    parts = [base.strip(), *(part.strip() for part in context if part.strip())]
    return "\n\n".join(part for part in parts if part)


def _to_wire(message: Message) -> dict[str, Any]:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.text}
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.text}
    if isinstance(message, SummaryMessage):
        tag = _SUMMARY_TAGS[message.kind]
        return {
            "role": "user",
            "content": (
                f'<{tag} source="{message.source_id}">\n{message.text}\n</{tag}>'
            ),
        }
    if isinstance(message, AssistantMessage):
        wire: dict[str, Any] = {"role": "assistant"}
        texts = [block.text for block in message.blocks if isinstance(block, TextBlock)]
        calls = [
            _tool_call_wire(block)
            for block in message.blocks
            if isinstance(block, ToolCallBlock)
        ]
        if texts:
            wire["content"] = "".join(texts)
        if calls:
            wire["tool_calls"] = calls
        if message.provider_state is not None:
            wire["_cade_replay_payload"] = message.provider_state
        if "content" not in wire and "tool_calls" not in wire:
            wire["content"] = ""
        return wire
    result: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": message.call_id,
        "content": message.content,
    }
    if message.images:
        result["images"] = [image.model_dump() for image in message.images]
    return result


def _tool_call_wire(block: ToolCallBlock) -> dict[str, Any]:
    return {
        "id": block.id,
        "type": "function",
        "function": {
            "name": block.name,
            "arguments": json.dumps(
                block.args,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        },
    }


def _estimate_messages(messages: Sequence[Message]) -> int:
    return sum(_estimate_message(message) for message in messages)


def _estimate_message(message: Message) -> int:
    if isinstance(message, SystemMessage | UserMessage):
        return 4 + _tokens(message.text)
    if isinstance(message, SummaryMessage):
        return 8 + _tokens(message.text) + _tokens(message.source_id)
    if isinstance(message, AssistantMessage):
        total = 4
        for block in message.blocks:
            if isinstance(block, TextBlock):
                total += _tokens(block.text)
            else:
                total += 4 + _tokens(block.name) + _tokens(_json(block.args))
        if message.provider_state is not None:
            total += _tokens(_json(message.provider_state))
        return total
    return 4 + _tokens(message.content) + 1600 * len(message.images)


def _estimate_tools(tools: Sequence[ToolDefinition]) -> int:
    total = 0
    for tool in tools:
        total += 4 + _tokens(tool.name) + _tokens(tool.description)
        total += _tokens(_json(tool.parameters))
    return total


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _digest(value: object) -> str:
    return hashlib.blake2b(
        _json(value).encode("utf-8"),
        digest_size=32,
    ).hexdigest()
