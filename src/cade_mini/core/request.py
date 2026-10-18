"""纯请求构造：投影 + 当前上下文 + 工具 + 预算。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, cast

from cade_mini.ai.types import StreamOptions, ThinkingBudgets, ToolDefinition
from cade_mini.core.types import (
    AssistantMessage,
    JsonValue,
    Message,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)

_TOOL_REFERENCE = (
    "[tool output omitted from this request; call_id={call_id}; "
    "use the history tool with call_id={call_id} for exact content]"
)
_KEEP_RECENT_TOOL_RESULTS = 4
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
    """本次请求的准入结果；丢弃项用于审计，不写回历史。"""

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
            options = {}
            for field in fields(self.options):
                if field.name in excluded:
                    continue
                value = getattr(self.options, field.name)
                options[field.name] = (
                    asdict(value) if isinstance(value, ThinkingBudgets) else value
                )
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
    """构造当前请求；不读文件、不写日志、不调用模型。"""
    system_text, messages = _prepare(conversation, context)

    tools_tokens = _estimate_tools(tools)
    message_budget = budget.input_budget - tools_tokens
    estimated = _estimate_messages(messages) + tools_tokens

    dropped_tool_calls: tuple[str, ...] = ()
    dropped_turns = 0
    if estimated > budget.input_budget:
        messages, dropped_tool_calls = _drop_tool_evidence(messages, message_budget)
        estimated = _estimate_messages(messages) + tools_tokens
    if estimated > budget.input_budget:
        messages, dropped_turns = _drop_turns(messages, message_budget)
        estimated = _estimate_messages(messages) + tools_tokens

    return Request(
        messages=tuple(_to_wire(message) for message in messages),
        tools=tuple(tools),
        options=options,
        report=BuildReport(
            estimated_tokens=estimated,
            budget_tokens=budget.input_budget,
            over_budget=estimated > budget.input_budget,
            dropped_tool_calls=dropped_tool_calls,
            dropped_turns=dropped_turns,
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


def _drop_tool_evidence(
    messages: list[Message],
    budget: int,
) -> tuple[list[Message], tuple[str, ...]]:
    """先替换旧工具结果；仍超预算时再动最近结果和最末一条。"""
    if budget <= 0:
        return messages, ()
    candidates = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, ToolResultMessage)
    ]
    protected = set(candidates[-_KEEP_RECENT_TOOL_RESULTS:])
    projected = list(messages)
    dropped: list[str] = []
    pruned: set[int] = set()

    def prune(index: int) -> None:
        message = projected[index]
        if not isinstance(message, ToolResultMessage) or index in pruned:
            return
        projected[index] = message.model_copy(
            update={"content": _TOOL_REFERENCE.format(call_id=message.call_id)}
        )
        dropped.append(message.call_id)
        pruned.add(index)

    for index in candidates:
        if index in protected:
            continue
        if _estimate_messages(projected) <= budget:
            return projected, tuple(dropped)
        prune(index)
    for index in candidates:
        if index not in protected or index == candidates[-1]:
            continue
        if _estimate_messages(projected) <= budget:
            return projected, tuple(dropped)
        prune(index)
    if _estimate_messages(projected) > budget and candidates:
        prune(candidates[-1])
    return projected, tuple(dropped)


def _drop_turns(messages: list[Message], budget: int) -> tuple[list[Message], int]:
    """从最旧的用户轮次开始整轮移除，保留最新任务。"""
    if budget <= 0:
        return messages, 0
    projected = list(messages)
    dropped = 0
    while _estimate_messages(projected) > budget:
        user_indices = [
            index
            for index, message in enumerate(projected)
            if isinstance(message, UserMessage)
        ]
        if len(user_indices) <= 1:
            break
        start, end = user_indices[0], user_indices[1]
        del projected[start:end]
        dropped += 1
    return projected, dropped


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
            wire["_cade_mini_replay_payload"] = message.provider_state
        if "content" not in wire and "tool_calls" not in wire:
            wire["content"] = ""
        return wire
    return {
        "role": "tool",
        "tool_call_id": message.call_id,
        "content": message.content,
    }


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
    return 4 + _tokens(message.content)


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
