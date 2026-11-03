"""Cade 的压缩：摘要旧历史，保留完整的近期模型与工具周期。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from cade.ai.events import (
    FinalMessage,
    ProviderFailure,
    TextDelta,
    UsageUpdate,
)
from cade.harness.provider import Provider
from cade.harness.request import Budget, estimate
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import (
    AssistantMessage,
    JsonValue,
    LogEntry,
    Message,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)

_SUMMARY_SYSTEM = (
    "You summarize a coding session for another model that will continue the "
    "work. Keep exact file paths, commands, errors, decisions, and unresolved "
    "work. Do not invent facts. Reply with the summary only."
)
_SUMMARY_INSTRUCTIONS = """\
Create a compact checkpoint with these sections:
## Goal
## Constraints
## Progress
## Key decisions
## Next steps
## Critical context
Keep each section short. Preserve exact names and error text.
The retained history may start midway through a user task. Preserve the full
objective, user constraints, completed verification, and unfinished work so the
next model can continue without another user message."""
_MAX_SUMMARY_CHARS = 120_000


@dataclass(frozen=True)
class CompactionConfig:
    """压缩保留量与摘要输入上限。"""

    keep_recent_tokens: int = 20_000
    max_summary_chars: int = _MAX_SUMMARY_CHARS

    def __post_init__(self) -> None:
        if self.keep_recent_tokens <= 0:
            raise ValueError("keep_recent_tokens must be positive")
        if self.max_summary_chars <= 0:
            raise ValueError("max_summary_chars must be positive")


class Compactor:
    """把压缩实现为可调用对象，交给 loop 的 compact 钩子。"""

    def __init__(
        self,
        provider: Provider,
        *,
        config: CompactionConfig | None = None,
        extra_context: Callable[[], str | None] | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or CompactionConfig()
        self.extra_context = extra_context

    async def __call__(
        self, log: SessionLog, budget: Budget, *, instructions: str | None = None
    ) -> bool:
        entries = log.branch()
        conversation = project(entries)
        cut = _select_cut(
            entries,
            conversation,
            keep_tokens=min(
                self.config.keep_recent_tokens,
                max(1, budget.input_budget // 3),
            ),
            hard_limit=budget.input_budget,
        )
        if cut is None:
            return False
        first_kept_id, messages_to_summarize = cut
        serialized = _serialize(messages_to_summarize)
        if not serialized.strip():
            return False
        text = _truncate(serialized, self.config.max_summary_chars)
        extra = self.extra_context() if self.extra_context is not None else None
        summary, usage, failure = await self._summarize(text, extra, instructions)
        if failure is not None:
            log.append(
                "attempt",
                {
                    "model": self.provider.model,
                    "provider": self.provider.transport,
                    "error": failure.message,
                    "usage": cast(JsonValue, usage),
                },
            )
            return False
        if not summary:
            log.append(
                "attempt",
                {
                    "model": self.provider.model,
                    "provider": self.provider.transport,
                    "error": "empty compaction summary",
                    "usage": cast(JsonValue, usage),
                },
            )
            return False
        log.append(
            "compaction",
            {
                "summary": summary,
                "first_kept_id": first_kept_id,
                "tokens_before": estimate(conversation),
                "model": self.provider.model,
                "provider": self.provider.transport,
                "usage": cast(JsonValue, usage),
                "details": {"summarized_messages": len(messages_to_summarize)},
            },
        )
        return True

    async def _summarize(
        self,
        serialized: str,
        extra_context: str | None,
        instructions: str | None = None,
    ) -> tuple[str, dict[str, int] | None, ProviderFailure | None]:
        text_parts: list[str] = []
        usage: dict[str, int] | None = None
        failure: ProviderFailure | None = None
        stop_reason = "end_turn"
        extra = (
            f"<working-note>\n{extra_context}\n</working-note>\n\n"
            if extra_context
            else ""
        )
        guidance = (
            f"\n\nAdditional instructions:\n{instructions}" if instructions else ""
        )
        messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{extra}<conversation>\n{serialized}\n</conversation>\n\n"
                    f"{_SUMMARY_INSTRUCTIONS}{guidance}"
                ),
            },
        ]
        try:
            async for event in self.provider.stream(messages, [], None):
                if isinstance(event, TextDelta):
                    text_parts.append(event.chunk)
                elif isinstance(event, UsageUpdate):
                    usage = {
                        "input": event.input_tokens,
                        "output": event.output_tokens,
                    }
                elif isinstance(event, FinalMessage):
                    stop_reason = event.stop_reason
                    if not text_parts and event.content:
                        text_parts.append(event.content)
                elif isinstance(event, ProviderFailure):
                    failure = event
        except Exception as exc:  # noqa: BLE001
            # Provider 库异常统一转成一次失败的摘要尝试。
            failure = ProviderFailure(
                message=str(exc),
                exception_type=type(exc).__name__,
            )
        if failure is not None:
            return "", usage, failure
        if stop_reason == "max_tokens":
            return (
                "",
                usage,
                ProviderFailure(
                    message="compaction summary hit max_tokens",
                    exception_type="SummaryTruncated",
                ),
            )
        return "".join(text_parts).strip(), usage, None


def _select_cut(
    entries: Sequence[LogEntry],
    conversation: Sequence[Message],
    *,
    keep_tokens: int,
    hard_limit: int,
) -> tuple[str, list[Message]] | None:
    positions = [
        index
        for index, message in enumerate(conversation)
        if isinstance(message, UserMessage | AssistantMessage)
    ]
    if len(positions) < 2:
        return None
    # 投影保留原始消息的后缀；空 assistant 不产生消息。
    message_entries = [
        entry
        for entry in entries
        if entry.type == "user"
        or (entry.type == "assistant" and entry.data.get("blocks"))
    ][-len(positions) :]
    candidates = list(zip(message_entries, positions, strict=True))[1:]
    users = [
        (entry, position) for entry, position in candidates if entry.type == "user"
    ]
    assistants = [
        (entry, position) for entry, position in candidates if entry.type == "assistant"
    ]
    # 优先保留完整用户轮次；目标保留量放不下时再拆分模型与工具周期。
    chosen: tuple[LogEntry, int] | None = None
    for boundaries in (users, assistants):
        chosen = next(
            (
                (entry, position)
                for entry, position in boundaries
                if estimate(conversation[position:]) <= keep_tokens
            ),
            None,
        )
        if chosen is not None:
            break
    if chosen is None:
        entry, position = candidates[-1]
        if estimate(conversation[position:]) > hard_limit:
            return None
        chosen = entry, position
    entry, position = chosen
    messages: list[Message] = [
        message
        for message in conversation[:position]
        if not isinstance(message, SystemMessage)
    ]
    return entry.id, messages


def _serialize(messages: Sequence[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, UserMessage):
            parts.append(f"USER:\n{message.text}")
        elif isinstance(message, SummaryMessage):
            parts.append(f"PRIOR SUMMARY:\n{message.text}")
        elif isinstance(message, AssistantMessage):
            texts = [
                block.text for block in message.blocks if isinstance(block, TextBlock)
            ]
            calls = [
                f"{block.name}({block.args})"
                for block in message.blocks
                if isinstance(block, ToolCallBlock)
            ]
            body = "\n".join(texts)
            if calls:
                body += "\nTOOL CALLS: " + ", ".join(calls)
            if body.strip():
                parts.append(f"ASSISTANT:\n{body}")
        elif isinstance(message, ToolResultMessage):
            status = "error" if message.is_error else "ok"
            parts.append(
                f"TOOL RESULT ({message.call_id}, {status}):\n{message.content}"
            )
    return "\n\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit - limit // 2) :]
    return f"{head}\n[...middle of conversation omitted...]\n{tail}"
