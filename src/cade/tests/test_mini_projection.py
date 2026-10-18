"""Cade Mini 投影的回归测试。"""

from __future__ import annotations

from typing import cast

import pytest

from cade.mini import (
    AssistantMessage,
    LogEntry,
    ProjectionError,
    SummaryMessage,
    ToolResultMessage,
    UserMessage,
    project,
)
from cade.mini.types import EventType, JsonValue


def _entry(
    entry_id: str,
    parent: str | None,
    event_type: str,
    data: dict[str, JsonValue],
) -> LogEntry:
    return LogEntry(
        id=entry_id,
        parent=parent,
        ts="2026-10-18T00:00:00+00:00",
        type=cast(EventType, event_type),
        data=data,
    )


def _system(entry_id: str, parent: str | None, text: str) -> LogEntry:
    return _entry(entry_id, parent, "system", {"text": text})


def _user(entry_id: str, parent: str | None, text: str) -> LogEntry:
    return _entry(entry_id, parent, "user", {"text": text, "source": "user"})


def _assistant(
    entry_id: str,
    parent: str | None,
    blocks: list[dict[str, JsonValue]],
) -> LogEntry:
    return _entry(
        entry_id,
        parent,
        "assistant",
        {
            "blocks": blocks,
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )


def _text(value: str) -> dict[str, JsonValue]:
    return {"type": "text", "text": value}


def _call(call_id: str, name: str = "bash") -> dict[str, JsonValue]:
    return {
        "type": "tool_call",
        "id": call_id,
        "name": name,
        "args": {"command": "pytest -q"},
    }


def _result(
    entry_id: str,
    parent: str | None,
    call_id: str,
    *,
    status: str = "ok",
    content: str = "passed",
) -> LogEntry:
    return _entry(
        entry_id,
        parent,
        "tool_result",
        {"call_id": call_id, "status": status, "content": content},
    )


def _compaction(
    entry_id: str,
    parent: str | None,
    summary: str,
    first_kept_id: str,
) -> LogEntry:
    return _entry(
        entry_id,
        parent,
        "compaction",
        {
            "summary": summary,
            "first_kept_id": first_kept_id,
            "tokens_before": 100,
            "model": "m",
            "provider": "p",
            "usage": None,
            "details": None,
        },
    )


def _window(
    entry_id: str,
    parent: str | None,
    start_id: str,
    handoff: str | None,
) -> LogEntry:
    return _entry(
        entry_id,
        parent,
        "window",
        {"start_id": start_id, "reason": "manual", "handoff": handoff},
    )


def test_basic_projection() -> None:
    entries = [
        _system("e1", None, "sys"),
        _user("e2", "e1", "hi"),
        _assistant("e3", "e2", [_text("hello")]),
        _user("e4", "e3", "again"),
    ]

    messages = project(entries)

    assert [message.role for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[3] == UserMessage(text="again")


def test_tool_call_and_result_are_paired() -> None:
    entries = [
        _user("e1", None, "run"),
        _assistant("e2", "e1", [_call("c1")]),
        _result("e3", "e2", "c1"),
        _assistant("e4", "e3", [_text("done")]),
    ]

    messages = project(entries)

    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
    ]
    assert messages[2] == ToolResultMessage(
        call_id="c1",
        content="passed",
        is_error=False,
    )


def test_open_tool_call_is_closed_at_the_tail() -> None:
    entries = [
        _user("e1", None, "run"),
        _assistant("e2", "e1", [_call("c1")]),
    ]

    messages = project(entries)

    assert isinstance(messages[-1], ToolResultMessage)
    assert messages[-1].call_id == "c1"
    assert messages[-1].is_error is True


def test_open_tool_call_before_later_message_is_rejected() -> None:
    entries = [
        _user("e1", None, "run"),
        _assistant("e2", "e1", [_call("c1")]),
        _user("e3", "e2", "next"),
    ]

    with pytest.raises(ProjectionError):
        project(entries)


def test_orphan_tool_result_is_rejected() -> None:
    entries = [
        _user("e1", None, "run"),
        _result("e2", "e1", "missing"),
    ]

    with pytest.raises(ProjectionError):
        project(entries)


def test_duplicate_tool_call_is_rejected() -> None:
    entries = [
        _user("e1", None, "run"),
        _assistant("e2", "e1", [_call("c1")]),
        _assistant("e3", "e2", [_call("c1")]),
        _result("e4", "e3", "c1"),
    ]

    with pytest.raises(ProjectionError):
        project(entries)


def test_compaction_emits_summary_and_retained_tail() -> None:
    entries = [
        _system("e1", None, "sys"),
        _user("e2", "e1", "old"),
        _assistant("e3", "e2", [_text("old answer")]),
        _user("e4", "e3", "new"),
        _assistant("e5", "e4", [_text("new answer")]),
        _compaction("e6", "e5", "summary", "e4"),
    ]

    messages = project(entries)

    assert [message.role for message in messages] == [
        "system",
        "summary",
        "user",
        "assistant",
    ]
    assert isinstance(messages[1], SummaryMessage)
    assert messages[1].text == "summary"
    assert messages[2] == UserMessage(text="new")


def test_compaction_cut_must_be_a_user_event() -> None:
    entries = [
        _user("e1", None, "old"),
        _assistant("e2", "e1", [_text("answer")]),
        _user("e3", "e2", "new"),
        _compaction("e4", "e3", "summary", "e2"),
    ]

    with pytest.raises(ProjectionError):
        project(entries)


def test_window_starts_at_start_id_and_keeps_handoff() -> None:
    entries = [
        _system("e1", None, "sys"),
        _user("e2", "e1", "old"),
        _assistant("e3", "e2", [_text("old answer")]),
        _user("e4", "e3", "start"),
        _assistant("e5", "e4", [_text("start answer")]),
        _window("e6", "e5", "e4", "handoff"),
        _user("e7", "e6", "next"),
    ]

    messages = project(entries)

    assert [message.role for message in messages] == [
        "system",
        "summary",
        "user",
        "assistant",
        "user",
    ]
    assert isinstance(messages[1], SummaryMessage)
    assert messages[1].text == "handoff"
    assert messages[2] == UserMessage(text="start")
    assert messages[4] == UserMessage(text="next")
    assert all(
        not isinstance(message, UserMessage) or message.text != "old"
        for message in messages
    )


def test_window_start_must_be_a_user_event() -> None:
    entries = [
        _user("e1", None, "old"),
        _assistant("e2", "e1", [_text("answer")]),
        _window("e3", "e2", "e2", None),
    ]

    with pytest.raises(ProjectionError):
        project(entries)


def test_system_is_not_shadowed_by_compaction() -> None:
    entries = [
        _system("e1", None, "sys"),
        _user("e2", "e1", "old"),
        _assistant("e3", "e2", [_text("answer")]),
        _user("e4", "e3", "new"),
        _compaction("e5", "e4", "summary", "e4"),
    ]

    messages = project(entries)

    assert messages[0].role == "system"
    assert isinstance(messages[1], SummaryMessage)


def test_empty_assistant_is_skipped() -> None:
    entries = [
        _system("e1", None, "sys"),
        _assistant("e2", "e1", []),
        _user("e3", "e2", "hi"),
    ]

    messages = project(entries)

    assert [message.role for message in messages] == ["system", "user"]


def test_projection_is_deterministic() -> None:
    entries = [
        _user("e1", None, "hi"),
        _assistant("e2", "e1", [_text("hello")]),
    ]

    assert project(entries) == project(entries)


def test_branch_must_start_at_root() -> None:
    with pytest.raises(ProjectionError):
        project([_user("e1", "missing", "hi")])


def test_assistant_message_carries_provider_state() -> None:
    entries = [
        _user("e1", None, "hi"),
        _entry(
            "e2",
            "e1",
            "assistant",
            {
                "blocks": [_text("hello")],
                "model": "m",
                "provider": "p",
                "usage": None,
                "provider_state": {"items": [{"type": "message"}]},
            },
        ),
    ]

    messages = project(entries)

    assert isinstance(messages[1], AssistantMessage)
    assert messages[1].provider_state == {"items": [{"type": "message"}]}
