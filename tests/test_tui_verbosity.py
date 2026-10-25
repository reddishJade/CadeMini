"""TUI 显示层级只筛选展示内容，不改变消息与工具记录。"""

from __future__ import annotations

import pytest

from cade.hosts.tui.compat import AgentHarnessResult, FinalStructuredEvent
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
    assert "Read README.md" in high

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


def _auto_live_state() -> _TuiState:
    state = _TuiState()
    state.set_verbosity("auto")
    state.add_user("First task")
    state.start_working()
    state.thinking = "Secret reasoning body"
    state._finish_thinking()
    state._record_tool_use("read-a", "read", {"path": "alpha.py"})
    state._record_tool_result("read-a", "ok", "read output alpha")
    state._record_tool_use("read-b", "read", {"path": "beta.py"})
    state._record_tool_result("read-b", "ok", "read output beta")
    state.log.append(_LogEntry("cade", "Progress update", markdown=True))
    state._record_tool_use("edit-a", "edit", {"path": "alpha.py"})
    state._record_tool_result("edit-a", "ok", "edit output")
    return state


def test_auto_live_shows_individual_calls_with_folded_thinking() -> None:
    state = _auto_live_state()
    visible = state.render()

    assert state.running
    assert "Thinking" in visible
    assert "Secret reasoning body" not in visible
    assert visible.count("Read alpha.py") == 1
    assert visible.count("Read beta.py") == 1
    assert "Progress update" in visible
    assert "Activity ·" not in visible
    assert "Exploring" not in visible
    assert "read output" not in visible
    assert "edit output" not in visible
    assert visible.index("Read alpha.py") < visible.index("Read beta.py")
    assert visible.index("Read beta.py") < visible.index("Progress update")
    assert visible.index("Progress update") < visible.index("Edit")


def test_auto_completion_folds_entire_turn_but_keeps_messages() -> None:
    state = _auto_live_state()
    before = [(entry.role, entry.content()) for entry in state.log]
    state.handle_event(
        FinalStructuredEvent(AgentHarnessResult(answer="Finished result"))
    )

    visible = state.render()
    assert not state.running
    assert visible.count("Activity · 3 tools · 1 thoughts") == 1
    assert "Progress update" in visible
    assert "Finished result" in visible
    assert "Read alpha.py" not in visible
    assert "Read beta.py" not in visible
    assert "Secret reasoning body" not in visible
    assert [(entry.role, entry.content()) for entry in state.log[: len(before)]] == before

    state.toggle_tools()
    expanded = state.render()
    assert state.verbosity == "auto"
    assert "Activity ·" not in expanded
    assert "Read alpha.py" in expanded
    assert "Read beta.py" in expanded
    assert "Secret reasoning body" not in expanded

    state.set_verbosity("high")
    assert "Secret reasoning body" in state.render()


def test_auto_new_turn_keeps_previous_activity_folded() -> None:
    state = _auto_live_state()
    state.handle_event(FinalStructuredEvent(AgentHarnessResult(answer="First done")))
    state.add_user("Second task")
    state.start_working()
    state._record_tool_use("second-read", "read", {"path": "second.py"})
    state._record_tool_result("second-read", "ok", "second output")

    during = state.render()
    assert "Activity · 3 tools · 1 thoughts" in during
    assert "Read second.py" in during
    assert "Read alpha.py" not in during
    assert "First done" in during

    state.handle_event(FinalStructuredEvent(AgentHarnessResult(answer="Second done")))
    after = state.render()
    assert after.count("Activity ·") == 2
    assert "Second done" in after
    assert "Read second.py" not in after


def test_auto_keeps_permission_notices_and_tool_failures_folded() -> None:
    state = _TuiState()
    state.set_verbosity("auto")
    state.add_user("Check security")
    state.start_working()
    state._record_tool_use("read-a", "read", {"path": "guard.py"})
    state._record_tool_result(
        "read-a", "ok", "private content", permission_notice="Allowed by rule"
    )
    state._record_tool_use("bash-a", "bash", {"command": "false"})
    state._record_tool_result("bash-a", "error", "secret failure detail")
    visible = state.render()
    assert "Allowed by rule" in visible
    assert "✗ " in visible
    assert "secret failure detail" not in visible

    state.handle_event(
        FinalStructuredEvent(AgentHarnessResult(answer="Tool failed", stop_reason="error"))
    )
    visible = state.render()
    assert "Allowed by rule" in visible
    assert "Activity · 2 tools · 0 thoughts" in visible
    assert "Tool failed" in visible


def test_auto_switching_modes_mid_run_does_not_change_tool_history() -> None:
    state = _auto_live_state()
    before = [(entry.role, entry.content()) for entry in state.log]
    state.set_verbosity("low")
    assert "Read alpha.py" not in state.render()
    state.set_verbosity("auto")
    assert "Read alpha.py" in state.render()
    state.set_verbosity("high")
    assert "read output alpha" not in state.render()
    state.set_verbosity("auto")
    assert [(entry.role, entry.content()) for entry in state.log] == before
