"""Cade 显式换窗的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import JsonValue, SummaryMessage
from cade.harness.window import reset_window


def _log(tmp_path: Path) -> SessionLog:
    return SessionLog.create(tmp_path / "sessions", session_id="w1")


def _assistant(text: str) -> dict[str, JsonValue]:
    return {
        "blocks": [{"type": "text", "text": text}],
        "model": "m",
        "provider": "p",
        "usage": None,
        "provider_state": None,
    }


def test_reset_window_shadows_old_history(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "old", "source": "user"})
    log.append("assistant", _assistant("old answer"))
    log.append("user", {"text": "new", "source": "user"})
    log.append("assistant", _assistant("new answer"))

    assert reset_window(log, handoff="handoff") is True

    entries = log.branch()
    assert entries[-1].type == "window"
    assert entries[-1].data["start_id"] == entries[-3].id
    messages = project(entries)
    assert [message.role for message in messages] == [
        "system",
        "summary",
        "user",
        "assistant",
    ]
    assert isinstance(messages[1], SummaryMessage)
    assert messages[1].kind == "window"
    assert messages[1].text == "handoff"
    assert all(
        not hasattr(message, "text") or "old" not in str(message.text)
        for message in messages[2:]
    )


def test_reset_window_is_idempotent_for_same_start(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "task", "source": "user"})

    assert reset_window(log) is True
    assert reset_window(log) is False


def test_reset_window_requires_a_user_task(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("system", {"text": "sys"})

    assert reset_window(log) is False


def test_rollover_preserves_task_and_steering_but_clears_execution(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "previous task"})
    log.append("assistant", _assistant("previous answer"))
    task = log.append("user", {"text": "finish backend"})
    log.append("assistant", _assistant("stale execution " * 100))
    log.append("user", {"text": "also verify migrations", "source": "runtime"})
    before = log.entries()
    assert reset_window(log, clear_history=True, handoff="Read NOTE.md")
    assert log.entries()[:-1] == before
    assert log.branch()[-1].data["start_id"] == task.id
    visible = project(log.branch())
    assert [message.role for message in visible] == ["system", "summary"]
    assert isinstance(visible[-1], SummaryMessage)
    assert "finish backend" in visible[-1].text
    assert "also verify migrations" in visible[-1].text
    assert "Read NOTE.md" in visible[-1].text
    assert "previous task" not in visible[-1].text
    assert "stale execution" not in visible[-1].text
    assert not reset_window(log, clear_history=True)
    reopened = SessionLog.open(log.path)
    assert project(reopened.branch()) == visible
    copied = reopened.copy_branch(tmp_path / "copies")
    assert project(copied.branch())[-1].text == visible[-1].text
    log.append("assistant", _assistant("more work"))
    assert reset_window(log, clear_history=True)
    assert "more work" not in str(project(log.branch()))


def test_rollover_waits_for_the_whole_tool_batch(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "task"})
    log.append(
        "assistant",
        {
            "blocks": [
                {"type": "tool_call", "id": call_id, "name": "read", "args": {}}
                for call_id in ("a", "b")
            ]
        },
    )
    log.append("tool_result", {"call_id": "a", "status": "ok", "content": "done"})
    before = log.entries()
    assert not reset_window(log, clear_history=True)
    assert log.entries() == before
    log.append(
        "tool_result",
        {"call_id": "b", "status": "interrupted", "content": "interrupted"},
    )
    assert reset_window(log, clear_history=True)


@pytest.mark.parametrize("source", ["user", "goal"])
def test_rollover_keeps_goal_root_and_steering_without_repeated_prompts(
    tmp_path: Path, source: str
) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "unrelated previous task"})
    log.append("assistant", _assistant("done"))
    log.append("run", {"termination": "completed", "steps": 1})
    root = log.append("user", {"text": "finish current backend", "source": source})
    log.append("user", {"text": "verify migrations too", "source": "runtime"})
    log.append("assistant", _assistant("some progress"))
    log.append("user", {"text": "goal round reminder", "source": "goal"})
    log.append("assistant", _assistant("more progress"))
    assert reset_window(log, clear_history=True)
    window = log.branch()[-1]
    assert window.data["start_id"] == root.id
    assert "finish current backend" in str(window.data["handoff"])
    assert "verify migrations too" in str(window.data["handoff"])
    assert "goal round reminder" not in str(window.data["handoff"])
    assert "unrelated previous task" not in str(window.data["handoff"])
