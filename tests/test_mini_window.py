"""Cade Mini 显式换窗的回归测试。"""

from __future__ import annotations

from pathlib import Path

from cade_mini.core.log import SessionLog
from cade_mini.core.projection import project
from cade_mini.core.types import JsonValue, SummaryMessage
from cade_mini.core.window import reset_window


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
