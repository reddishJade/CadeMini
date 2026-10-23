"""把会话事实投影为确定性的模型消息序列。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from cade.harness.session.types import (
    AssistantMessage,
    EventType,
    JsonValue,
    LogEntry,
    Message,
    ProjectionError,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)

_CONVERSATION_TYPES: frozenset[EventType] = frozenset(
    {"system", "user", "assistant", "tool_result", "compaction", "window"}
)
_LOG_ONLY_TYPES: frozenset[EventType] = frozenset(
    {"meta", "request", "run", "attempt", "branch", "checkpoint", "restore"}
)
_TOOL_RESULT_STATUSES = frozenset({"ok", "error", "interrupted"})


def project(entries: Sequence[LogEntry]) -> list[Message]:
    """从一条分支的日志事实重建模型可见消息。

    纯函数：不读文件、不写日志、不调用模型。输入不合法时抛出
    ProjectionError，而不是静默修补。
    """
    if not entries:
        return []
    _validate_branch(entries)
    by_id = {entry.id: index for index, entry in enumerate(entries)}

    start = 0
    handoff: str | None = None
    window_entry: LogEntry | None = None
    window = _latest(entries, "window")
    if window is not None:
        window_index, window_entry = window
        start = _index_of(by_id, window_entry, "start_id")
        if start >= window_index:
            raise ProjectionError("window start must precede the window event")
        if entries[start].type != "user":
            raise ProjectionError("window start must be a user event")
        handoff = _optional_text(window_entry.data, "handoff")

    first_kept = start
    summary: str | None = None
    compaction_entry: LogEntry | None = None
    compaction = _latest(entries, "compaction", start=start)
    if compaction is not None:
        compaction_index, compaction_entry = compaction
        first_kept = _index_of(by_id, compaction_entry, "first_kept_id")
        if not start <= first_kept < compaction_index:
            raise ProjectionError(
                "compaction first_kept_id must be a retained entry before it"
            )
        if entries[first_kept].type != "user":
            raise ProjectionError("compaction first_kept_id must be a user event")
        summary = _text(compaction_entry.data, "summary")

    messages: list[Message] = []
    system = _latest(entries, "system")
    if system is not None:
        messages.append(SystemMessage(text=_text(system[1].data, "text")))
    if handoff and window_entry is not None:
        messages.append(
            SummaryMessage(text=handoff, source_id=window_entry.id, kind="window")
        )
    if summary is not None and compaction_entry is not None:
        messages.append(SummaryMessage(text=summary, source_id=compaction_entry.id))

    for entry in entries[first_kept:]:
        if entry.type in _LOG_ONLY_TYPES:
            continue
        if entry.type in {"system", "window", "compaction"}:
            continue
        if entry.type == "user":
            messages.append(_parse_user(entry))
        elif entry.type == "assistant":
            assistant = _parse_assistant(entry)
            if assistant is not None:
                messages.append(assistant)
        elif entry.type == "tool_result":
            messages.append(_parse_tool_result(entry))
        else:
            raise ProjectionError(f"unknown event type: {entry.type}")

    _close_tool_calls(messages)
    return messages


def _validate_branch(entries: Sequence[LogEntry]) -> None:
    """校验输入是从根开始的一条连续分支。"""
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if entry.id in seen:
            raise ProjectionError(f"duplicate event id: {entry.id}")
        if index == 0:
            if entry.parent is not None:
                raise ProjectionError("projection input must start at the branch root")
        elif entry.parent not in seen:
            raise ProjectionError(f"missing or forward parent: {entry.parent}")
        if entry.type not in _CONVERSATION_TYPES | _LOG_ONLY_TYPES:
            raise ProjectionError(f"unknown event type: {entry.type}")
        seen.add(entry.id)


def _latest(
    entries: Sequence[LogEntry],
    event_type: EventType,
    *,
    start: int = 0,
) -> tuple[int, LogEntry] | None:
    for index in range(len(entries) - 1, start - 1, -1):
        if entries[index].type == event_type:
            return index, entries[index]
    return None


def _index_of(
    by_id: dict[str, int],
    entry: LogEntry,
    field: str,
) -> int:
    target = _text(entry.data, field)
    index = by_id.get(target)
    if index is None:
        raise ProjectionError(f"{entry.type}.{field} does not name an entry: {target}")
    return index


def _parse_user(entry: LogEntry) -> UserMessage:
    source = _optional_text(entry.data, "source") or "user"
    if source not in {"user", "runtime"}:
        raise ProjectionError(f"invalid user source: {source}")
    return UserMessage(
        text=_text(entry.data, "text"),
        source="runtime" if source == "runtime" else "user",
    )


def _parse_assistant(entry: LogEntry) -> AssistantMessage | None:
    raw_blocks = entry.data.get("blocks")
    if not isinstance(raw_blocks, list):
        raise ProjectionError("assistant.blocks must be a list")
    blocks: list[TextBlock | ToolCallBlock] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            raise ProjectionError("assistant block must be an object")
        kind = raw.get("type")
        if kind == "text":
            text = raw.get("text")
            if not isinstance(text, str):
                raise ProjectionError("text block requires a string text")
            blocks.append(TextBlock(text=text))
            continue
        if kind == "tool_call":
            call_id = raw.get("id")
            name = raw.get("name")
            args = raw.get("args", {})
            if not isinstance(call_id, str) or not call_id:
                raise ProjectionError("tool call requires a non-empty id")
            if not isinstance(name, str) or not name:
                raise ProjectionError("tool call requires a non-empty name")
            if not isinstance(args, dict):
                raise ProjectionError("tool call args must be an object")
            blocks.append(
                ToolCallBlock(
                    id=call_id,
                    name=name,
                    args=cast(dict[str, JsonValue], args),
                )
            )
            continue
        raise ProjectionError(f"unknown assistant block type: {kind!r}")
    if not blocks:
        return None
    return AssistantMessage(
        blocks=tuple(blocks),
        provider_state=_optional_object(entry.data, "provider_state"),
    )


def _parse_tool_result(entry: LogEntry) -> ToolResultMessage:
    from cade.harness.session.types import ImageAttachment

    status = _text(entry.data, "status")
    if status not in _TOOL_RESULT_STATUSES:
        raise ProjectionError(f"invalid tool result status: {status}")
    artifact = entry.data.get("artifact")
    metadata = artifact.get("tool_metadata") if isinstance(artifact, dict) else None
    images = metadata.get("images") if isinstance(metadata, dict) else None
    return ToolResultMessage(
        call_id=_text(entry.data, "call_id"),
        content=_text(entry.data, "content"),
        is_error=status != "ok",
        images=tuple(ImageAttachment.model_validate(image) for image in images)
        if isinstance(images, list)
        else (),
    )


def _close_tool_calls(messages: list[Message]) -> None:
    """配对调用与结果；仅允许末尾留下未完成调用并补中断结果。"""
    call_index: dict[str, int] = {}
    completed: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            for block in message.blocks:
                if not isinstance(block, ToolCallBlock):
                    continue
                if block.id in call_index:
                    raise ProjectionError(f"duplicate tool call: {block.id}")
                call_index[block.id] = index
        elif isinstance(message, ToolResultMessage):
            if message.call_id not in call_index:
                raise ProjectionError(f"orphan tool result: {message.call_id}")
            if message.call_id in completed:
                raise ProjectionError(f"duplicate tool result: {message.call_id}")
            completed.add(message.call_id)

    missing = set(call_index) - completed
    if not missing:
        return
    first_open = min(call_index[call_id] for call_id in missing)
    for message in messages[first_open + 1 :]:
        if isinstance(message, AssistantMessage | UserMessage | SummaryMessage):
            raise ProjectionError(
                "open tool call must be closed before the next message"
            )
    for message in messages[first_open:]:
        if not isinstance(message, AssistantMessage):
            continue
        for block in message.blocks:
            if isinstance(block, ToolCallBlock) and block.id in missing:
                messages.append(
                    ToolResultMessage(
                        call_id=block.id,
                        content="Tool execution was interrupted.",
                        is_error=True,
                    )
                )


def _text(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ProjectionError(f"event field {key} must be a string")
    return value


def _optional_text(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectionError(f"event field {key} must be a string or null")
    return value


def _optional_object(
    data: dict[str, JsonValue],
    key: str,
) -> dict[str, JsonValue] | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProjectionError(f"event field {key} must be an object or null")
    return cast(dict[str, JsonValue], value)
