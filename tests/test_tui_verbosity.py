"""TUI 显示层级只筛选展示内容，不改变消息与工具记录。"""

from __future__ import annotations

import pytest

from cade.hosts.tui.state import _LogEntry, _TuiState


def _populated_state() -> _TuiState:
    state = _TuiState()
    state.add_user("User request marker")
    state.log.append(_LogEntry("cade", "Progress message marker", markdown=True))
    state.log.append(
        _LogEntry("thinking", "Private reasoning marker\nThought for 1.0s")
    )
    state.thinking = "Live reasoning marker"
    state._record_tool_use("shell-1", "bash", {"command": "printf shell-marker"})
    state._record_tool_result(
        "shell-1",
        "ok",
        "Tool result marker",
        permission_notice="Permission notice marker",
    )
    state._record_tool_use("read-1", "read", {"path": "README.md"})
    state._record_tool_result(
        "read-1", "ok", "Read result marker", permission_notice="Read allowed marker"
    )
    state.log.append(_LogEntry("response-metrics", "TTFT 0.50s · TPS 50"))
    return state


def test_low_shows_messages_and_permission_notices_without_tool_transcripts() -> None:
    state = _populated_state()
    retained = [(entry.role, entry.content()) for entry in state.log]
    state.set_verbosity("low")

    rendered = state.render()
    assert "User request marker" in rendered
    assert "Progress message marker" in rendered
    assert "Permission notice marker" in rendered
    assert "Read allowed marker" in rendered
    for hidden in (
        "Private reasoning marker",
        "Live reasoning marker",
        "Thought for",
        "shell-marker",
        "Tool result marker",
        "Read result marker",
        "README.md",
        "TTFT",
    ):
        assert hidden not in rendered

    assert [(entry.role, entry.content()) for entry in state.log] == retained
    assert any(entry.role == "tool-detail" for entry in state.log)


def test_switching_verbosity_reprojects_same_events() -> None:
    state = _populated_state()

    state.set_verbosity("medium")
    medium = state.render()
    assert "Progress message marker" in medium
    assert "Thinking" in medium
    assert "Private reasoning marker" not in medium
    assert "Tool result marker" not in medium
    assert "Read result marker" not in medium
    assert "TTFT" in medium

    state.set_verbosity("high")
    high = state.render()
    assert "Private reasoning marker" in high
    assert "Live reasoning marker" in high
    assert "Tool result marker" in high
    assert "Read result marker" in high

    state.set_verbosity("low")
    assert "Tool result marker" not in state.render()
    assert "Progress message marker" in state.render()


def test_shortcuts_leave_low_and_restore_manual_fold_controls() -> None:
    state = _populated_state()
    state.set_verbosity("low")

    state.toggle_tools()
    assert state.verbosity == "medium"
    assert not state.tool_collapsed
    assert "Tool result marker" in state.render()

    state.set_verbosity("low")
    state.toggle_thinking()
    assert state.verbosity == "medium"
    assert not state.thinking_collapsed
    assert "Private reasoning marker" in state.render()


def test_low_preserves_live_permission_prompt() -> None:
    state = _populated_state()
    state.set_verbosity("low")
    state.log.append(_LogEntry("system", "Approval required marker"))
    assert "Approval required marker" in state.render()


def test_restored_events_stay_available_after_low_hides_them() -> None:
    state = _TuiState()
    state.set_verbosity("low")
    state._restore_event(
        {
            "type": "tool_use",
            "data": {
                "id": "restored-1",
                "name": "bash",
                "input": {"command": "printf restored-command-marker"},
            },
        }
    )
    state._restore_event(
        {
            "type": "tool_result",
            "data": {
                "tool_use_id": "restored-1",
                "status": "ok",
                "content": "Restored tool result marker",
            },
        }
    )
    assert "Restored tool result marker" not in state.render()

    state.set_verbosity("high")
    assert "Restored tool result marker" in state.render()


def test_invalid_level_keeps_presentation_unchanged() -> None:
    state = _populated_state()
    state.set_verbosity("low")
    before = state.render()
    with pytest.raises(ValueError, match="Unknown display verbosity"):
        state.set_verbosity("off")
    assert state.verbosity == "low"
    assert state.render() == before
