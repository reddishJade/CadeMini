"""Cade Mini 会话日志的回归测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

from cade_mini.core.log import SessionLog, list_sessions, session_title
from cade_mini.core.projection import project
from cade_mini.core.request import Budget, build
from cade_mini.core.types import EventType, JsonValue, LogError, UserMessage


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


def test_deferred_session_stays_in_memory_until_first_message(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), deferred=True)
    log.append("system", {"text": "sys"})
    assert [entry.type for entry in log.branch()] == ["meta", "system"]
    assert not list(_sessions(tmp_path).glob("session-*"))

    log.append("user", {"text": "first task"})
    reopened = SessionLog.open(log.path)
    assert [entry.type for entry in reopened.branch()] == ["meta", "system", "user"]
    assert reopened.branch()[-1].data["text"] == "first task"
    log.append("assistant", {"blocks": [{"type": "text", "text": "answer"}]})
    assert SessionLog.open(log.path).branch()[-1].type == "assistant"


def test_deferred_session_does_not_overwrite_a_competing_session(
    tmp_path: Path,
) -> None:
    draft = SessionLog.create(_sessions(tmp_path), session_id="shared", deferred=True)
    existing = SessionLog.create(_sessions(tmp_path), session_id="shared")
    original = existing.path.read_bytes()
    with pytest.raises(LogError, match="session already exists"):
        draft.append("user", {"text": "must not overwrite"})
    assert existing.path.read_bytes() == original


def test_fork_before_first_input_can_remain_unsaved(tmp_path: Path) -> None:
    source = SessionLog.create(_sessions(tmp_path))
    source.append("system", {"text": "sys"})
    turn = source.append("user", {"text": "first task"})
    fork = source.copy_branch(
        _sessions(tmp_path), before_entry_id=turn.id, deferred=True
    )
    assert not fork.path.exists()
    assert [entry.type for entry in fork.branch()] == ["meta", "system"]
    fork.append("user", {"text": "alternative"})
    assert SessionLog.open(fork.path).branch()[-1].data["text"] == "alternative"
    assert source.branch()[-1].data["text"] == "first task"


def test_stale_writers_keep_all_entries_on_active_branch(tmp_path: Path) -> None:
    first = SessionLog.create(_sessions(tmp_path), session_id="shared")
    second = SessionLog.open(first.path)

    one = first.append("user", {"text": "one"})
    two = second.append("user", {"text": "two"})
    three = first.append("user", {"text": "three"})

    assert two.parent == one.id
    assert three.parent == two.id
    assert first.branch() == first.entries()


def test_process_writers_keep_one_continuous_branch(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="processes")
    script = (
        "from pathlib import Path; import sys; "
        "from cade_mini.core.log import SessionLog; "
        "log = SessionLog.open(Path(sys.argv[1])); "
        "[log.append('user', {'text': str(i)}) for i in range(10)]"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", script, str(log.path)])
        for _ in range(4)
    ]
    try:
        for process in processes:
            assert process.wait(timeout=30) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()

    log.append("user", {"text": "after processes"})
    entries = log.entries()
    assert len(entries) == 42
    assert log.branch() == entries
    assert all(entry.parent == previous.id for previous, entry in pairwise(entries))


def test_run_lock_rejects_another_process_and_releases(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="locked")
    script = (
        "from pathlib import Path; import sys; "
        "from cade_mini.core.log import SessionLog; "
        "log = SessionLog.open(Path(sys.argv[1])); "
        "exec('with log.run_lock():\\n pass')"
    )
    with log.run_lock():
        result = subprocess.run(
            [sys.executable, "-c", script, str(log.path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode != 0
        assert "session is already running" in result.stderr
    result = subprocess.run(
        [sys.executable, "-c", script, str(log.path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_append_writes_one_complete_line_per_fact(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="s2")
    log.append("user", {"text": "one", "source": "user"})
    log.append("user", {"text": "two", "source": "user"})

    raw = log.path.read_bytes()
    assert raw.endswith(b"\n")
    lines = raw.splitlines()
    assert len(lines) == 3
    assert all(json.loads(line)["v"] == 4 for line in lines)


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


def test_v1_session_resumes_without_rewriting_old_facts(tmp_path: Path) -> None:
    path = _sessions(tmp_path) / "session-legacy.jsonl"
    path.parent.mkdir()
    original = (
        b'{"v":1,"id":"old","parent":null,"ts":"2026-10-18T00:00:00+00:00",'
        b'"type":"user","data":{"text":"legacy task","source":"user"}}\n'
    )
    path.write_bytes(original)
    log = SessionLog.open(path)
    before = project(log.branch())
    entry = log.append("assistant", {"blocks": [{"type": "text", "text": "answer"}]})
    assert entry.v == 4
    assert entry.parent == "old"
    assert path.read_bytes().startswith(original)
    assert project(SessionLog.open(path).branch())[:1] == before


def test_future_session_version_is_rejected(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="future")
    record = json.loads(log.path.read_text(encoding="utf-8"))
    record["v"] = 5
    log.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(LogError, match="invalid session record"):
        SessionLog.open(log.path)


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        ("meta", {"session": "s"}),
        ("system", {"text": 42}),
        ("user", {"text": "task", "source": "unknown"}),
        ("user", {"text": "task", "typo": True}),
        ("assistant", {"blocks": [{"type": "unknown"}]}),
        ("assistant", {"blocks": [{"type": "tool_call", "id": "", "name": "echo"}]}),
        ("tool_result", {"call_id": "c1", "status": "success", "content": "ok"}),
        ("compaction", {"summary": "summary", "first_kept_id": 12}),
        ("window", {"start_id": "u1", "reason": "implicit"}),
        ("request", {"model": "m"}),
        ("run", {"termination": "finished", "steps": 1}),
        ("run", {"termination": "completed", "steps": True}),
        (
            "attempt",
            {
                "model": "m",
                "provider": "p",
                "error": "bad",
                "usage": {"input": -1, "output": 0},
            },
        ),
        (
            "assistant",
            {
                "blocks": [
                    {
                        "type": "tool_call",
                        "id": "c1",
                        "name": "echo",
                        "args": {"value": float("nan")},
                    }
                ]
            },
        ),
        ("unknown", {}),
    ],
)
def test_invalid_payload_is_rejected_before_tail_repair(
    tmp_path: Path, event_type: str, data: dict[str, JsonValue]
) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="payload")
    head = log.head_id
    with log.path.open("ab") as stream:
        stream.write(b'{"incomplete":')
    before = log.path.read_bytes()
    with pytest.raises(LogError, match="event payload"):
        log.append(cast(EventType, event_type), data)
    assert log.path.read_bytes() == before
    assert log.head_id == head


@pytest.mark.parametrize(
    "failure", ["tools", "budget", "options", "option_type", "option_name"]
)
def test_request_payload_validates_nested_snapshot(
    tmp_path: Path, failure: str
) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="audit")
    snapshot = build([UserMessage(text="task")], budget=Budget(100_000)).as_record()
    if failure == "tools":
        snapshot["tools"] = [{"name": "echo", "description": "Echo", "parameters": []}]
    elif failure == "budget":
        snapshot["budget"] = {
            "context_window": 1,
            "output_reserve": 2,
            "headroom_tokens": 0,
        }
    elif failure == "options":
        snapshot["options"] = {"api_key": "must not persist"}
    elif failure == "option_type":
        snapshot["options"] = {"max_tokens": "many"}
    else:
        snapshot["options"] = {"unknown_option": True}
    before = log.path.read_bytes()
    with pytest.raises(LogError, match="request event payload"):
        log.append(
            "request",
            {
                "model": "m",
                "provider": "p",
                "tools_digest": "digest",
                "conversation_digest": "digest",
                "context": [],
                "estimated_tokens": 1,
                "dropped": [],
                "snapshot": snapshot,
            },
        )
    assert log.path.read_bytes() == before


def test_copy_preserves_legacy_audit_version_without_inventing_snapshot(
    tmp_path: Path,
) -> None:
    path = _sessions(tmp_path) / "session-legacy-audit.jsonl"
    path.parent.mkdir()
    path.write_text(
        '{"v":1,"id":"old","parent":null,"ts":"2026-10-18T00:00:00+00:00",'
        '"type":"request","data":{"model":"m","provider":"p",'
        '"tools_digest":"t","conversation_digest":"c","context":[],"estimated_tokens":1,"dropped":[]}}\n',
        encoding="utf-8",
    )
    copied = SessionLog.open(path).copy_branch(_sessions(tmp_path))
    audit = next(entry for entry in copied.branch() if entry.type == "request")
    assert audit.v == 1
    assert "snapshot" not in audit.data


@pytest.mark.parametrize("version", [2, 3, 4])
def test_read_rejects_invalid_validated_payload(tmp_path: Path, version: int) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="invalid-data")
    records = [
        json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()
    ]
    records[0]["v"] = version
    records[0]["data"]["session"] = False
    log.path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(LogError, match="invalid session record"):
        SessionLog.open(log.path)


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
