"""Cade Mini 会话日志的回归测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cade_mini.core.log import SessionLog, list_sessions, session_title
from cade_mini.core.types import LogError


def _sessions(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


def test_create_append_and_branch(tmp_path: Path) -> None:
    log = SessionLog.create(
        _sessions(tmp_path),
        session_id="s1",
        project_root=tmp_path,
    )
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "hi", "source": "user"})
    assistant = log.append(
        "assistant",
        {
            "blocks": [{"type": "text", "text": "hello"}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )

    assert log.session_id == "s1"
    assert log.head_id == assistant.id
    branch = log.branch()
    assert [entry.type for entry in branch] == [
        "meta",
        "system",
        "user",
        "assistant",
    ]
    assert branch[-1].id == assistant.id
    assert branch[-1].parent == branch[-2].id


def test_append_writes_one_complete_line_per_fact(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="s2")
    log.append("user", {"text": "one", "source": "user"})
    log.append("user", {"text": "two", "source": "user"})

    raw = log.path.read_bytes()
    assert raw.endswith(b"\n")
    lines = raw.splitlines()
    assert len(lines) == 3
    assert all(json.loads(line)["v"] == 1 for line in lines)


def test_incomplete_tail_is_ignored_and_repaired(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="s3")
    log.append("user", {"text": "kept", "source": "user"})
    with log.path.open("ab") as stream:
        stream.write(b'{"v":1,"id":"broken"')

    assert [entry.type for entry in log.entries()] == ["meta", "user"]

    log.append("user", {"text": "after crash", "source": "user"})
    entries = log.entries()
    assert [entry.type for entry in entries] == ["meta", "user", "user"]
    assert entries[-1].data["text"] == "after crash"
    assert b"broken" not in log.path.read_bytes()


def test_open_rejects_non_session_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(LogError):
        SessionLog.open(path)


def test_read_rejects_forward_parent(tmp_path: Path) -> None:
    path = _sessions(tmp_path) / "session-bad.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "v": 1,
                "id": "e1",
                "parent": "missing",
                "ts": "2026-10-18T00:00:00+00:00",
                "type": "user",
                "data": {"text": "bad", "source": "user"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LogError):
        SessionLog.open(path).entries()


def test_create_rejects_existing_session(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    SessionLog.create(sessions, session_id="dup")
    with pytest.raises(LogError):
        SessionLog.create(sessions, session_id="dup")


def test_open_empty_session_file_starts_without_head(tmp_path: Path) -> None:
    path = _sessions(tmp_path) / "session-empty.jsonl"
    path.parent.mkdir(parents=True)
    path.touch()

    log = SessionLog.open(path)
    assert log.entries() == []
    assert log.head_id is None

    log.append("user", {"text": "first", "source": "user"})
    entries = log.entries()
    assert [entry.type for entry in entries] == ["user"]
    assert entries[0].parent is None


def test_list_sessions_titles_and_order(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    first = SessionLog.create(sessions, session_id="first")
    first.append("user", {"text": "first task", "source": "user"})
    second = SessionLog.create(sessions, session_id="second")
    second.append("user", {"text": "second task\nwith lines", "source": "user"})
    os.utime(first.path, (1, 1))
    os.utime(second.path, (2, 2))

    infos = list_sessions(sessions)

    assert [info.id for info in infos] == ["second", "first"]
    assert infos[0].title == "second task with lines"
    assert infos[1].title == "first task"
    assert session_title(first.branch()) == "first task"


def test_list_sessions_skips_corrupt_files(tmp_path: Path) -> None:
    sessions = _sessions(tmp_path)
    valid = SessionLog.create(sessions, session_id="valid")
    valid.append("user", {"text": "kept", "source": "user"})
    (sessions / "session-bad.jsonl").write_text(
        '{"v":1,"id":"broken"}\n',
        encoding="utf-8",
    )

    infos = list_sessions(sessions)

    assert [info.id for info in infos] == ["valid"]


def test_copy_branch_fork_clone_and_rewind(tmp_path: Path) -> None:
    log = SessionLog.create(
        _sessions(tmp_path), session_id="source", project_root=tmp_path
    )
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "one", "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [{"type": "text", "text": "a1"}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )
    second = log.append("user", {"text": "two", "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [{"type": "text", "text": "a2"}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )

    forked = log.copy_branch(
        _sessions(tmp_path), before_entry_id=second.id, project_root=tmp_path
    )
    assert [entry.type for entry in forked.branch()] == [
        "meta",
        "system",
        "user",
        "assistant",
    ]
    assert "two" not in [entry.data.get("text") for entry in forked.branch()]

    cloned = log.copy_branch(_sessions(tmp_path), project_root=tmp_path)
    assert [entry.type for entry in cloned.branch()][-1] == "assistant"
    assert cloned.session_id != log.session_id

    rewound = log.copy_branch(
        _sessions(tmp_path), drop_recent_users=1, project_root=tmp_path
    )
    assert [entry.type for entry in rewound.branch()] == [
        "meta",
        "system",
        "user",
        "assistant",
    ]
