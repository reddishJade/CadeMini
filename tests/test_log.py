"""Cade 会话日志的回归测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

from cade.harness.request import Budget, build
from cade.harness.session.log import SessionLog, list_sessions, session_title
from cade.harness.session.types import EventType, JsonValue, LogError, UserMessage


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


@pytest.mark.parametrize("deferred", [False, True])
def test_logs_request_private_permissions_at_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deferred: bool
) -> None:
    original = os.open
    created_modes: list[int] = []

    def open_file(path: str | Path, flags: int, mode: int = 0o777) -> int:
        if str(path).endswith(".jsonl") and flags & os.O_CREAT:
            created_modes.append(mode)
        return original(path, flags, mode)

    monkeypatch.setattr(os, "open", open_file)
    log = SessionLog.create(_sessions(tmp_path), deferred=deferred)
    log.append("user", {"text": "private task"})
    assert created_modes and set(created_modes) == {0o600}
    assert SessionLog.open(log.path).branch()[-1].data["text"] == "private task"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")
@pytest.mark.parametrize("deferred", [False, True])
def test_logs_are_private_under_permissive_umask(
    tmp_path: Path, deferred: bool
) -> None:
    previous = os.umask(0)
    try:
        log = SessionLog.create(_sessions(tmp_path), deferred=deferred)
        log.append("user", {"text": "private task"})
    finally:
        os.umask(previous)
    assert log.path.stat().st_mode & 0o777 == 0o600
    assert log.path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")
def test_open_tightens_old_log_without_changing_shared_directory(
    tmp_path: Path,
) -> None:
    directory = _sessions(tmp_path)
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    log = SessionLog.create(directory)
    log.append("user", {"text": "private task"})
    original = log.path.read_bytes()
    log.path.chmod(0o644)
    reopened = SessionLog.open(log.path)
    assert reopened.path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o755
    assert reopened.path.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX change timestamps")
def test_open_private_log_does_not_change_its_metadata(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path))
    log.append("user", {"text": "kept"})
    before = log.path.stat()
    SessionLog.open(log.path).entries()
    after = log.path.stat()
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns == before.st_ctime_ns


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


def test_returned_records_cannot_mutate_cached_history(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path))
    data: dict[str, JsonValue] = {"blocks": [{"type": "text", "text": "original"}]}
    returned = log.append("assistant", data)
    persisted = json.loads(log.path.read_text(encoding="utf-8").splitlines()[-1])[
        "data"
    ]
    returned.data.clear()
    data.clear()
    entries = log.entries()
    entries[-1].data.clear()
    branch = log.branch()
    branch[-1].data.clear()
    assert log.entries()[-1].data == persisted
    log.append("user", {"text": "next task"})
    assert log.entries() == SessionLog.open(log.path).entries()


def test_replaced_file_invalidates_cache_even_with_same_size_and_time(
    tmp_path: Path,
) -> None:
    log = SessionLog.create(_sessions(tmp_path))
    log.append("user", {"text": "one"})
    metadata = log.path.stat()
    original = log.path.read_bytes()
    replacement = log.path.with_suffix(".replacement")
    replacement.write_bytes(original.replace(b'"text":"one"', b'"text":"two"'))
    os.utime(replacement, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    os.replace(replacement, log.path)
    assert log.entries()[-1].data["text"] == "two"


def test_truncated_file_does_not_reuse_cached_head(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path))
    log.append("user", {"text": "discarded"})
    first_line = log.path.read_bytes().splitlines(keepends=True)[0]
    log.path.write_bytes(first_line)
    appended = log.append("user", {"text": "replacement"})
    assert appended.parent == json.loads(first_line)["id"]
    assert len(log.entries()) == 2
    assert log.entries() == SessionLog.open(log.path).entries()


def test_external_corruption_is_rejected_before_cached_append(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path))
    log.append("user", {"text": "kept"})
    with log.path.open("ab") as stream:
        stream.write(b'{"invalid":true}\n')
    corrupted = log.path.read_bytes()
    with pytest.raises(LogError, match="invalid session record"):
        log.append("user", {"text": "must not append"})
    assert log.path.read_bytes() == corrupted


def test_failed_sync_does_not_leave_a_stale_cached_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SessionLog.create(_sessions(tmp_path))

    def fail_sync(descriptor: int) -> None:
        raise OSError("simulated sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_sync)
        with pytest.raises(OSError, match="sync failure"):
            log.append("user", {"text": "uncertain commit"})
    committed = json.loads(log.path.read_text(encoding="utf-8").splitlines()[-1])
    appended = log.append("user", {"text": "after failure"})
    assert appended.parent == committed["id"]
    assert log.entries() == SessionLog.open(log.path).entries()


def test_process_writers_keep_one_continuous_branch(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="processes")
    script = (
        "from pathlib import Path; import sys; "
        "from cade.harness.session.log import SessionLog; "
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
        "from cade.harness.session.log import SessionLog; "
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
    assert all(
        set(json.loads(line)) == {"id", "parent", "ts", "type", "data"}
        for line in lines
    )


def test_incomplete_tail_is_ignored_and_repaired(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="s3")
    log.append("user", {"text": "kept", "source": "user"})
    with log.path.open("ab") as stream:
        stream.write(b'{"id":"broken"')

    assert [entry.type for entry in log.entries()] == ["meta", "user"]

    log.append("user", {"text": "after crash", "source": "user"})
    entries = log.entries()
    assert [entry.type for entry in entries] == ["meta", "user", "user"]
    assert entries[-1].data["text"] == "after crash"
    assert b"broken" not in log.path.read_bytes()


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        ("meta", {"session": "s"}),
        ("system", {"text": 42}),
        ("user", {"text": "task", "source": "unknown"}),
        ("user", {"text": "task", "typo": True}),
        ("assistant", {"blocks": [{"type": "unknown"}]}),
        ("assistant", {"blocks": [{"type": "tool_call", "id": "", "name": "echo"}]}),
        ("assistant", {"blocks": [], "reasoning": 42}),
        ("assistant", {"blocks": [], "response_metrics": {"ttft_seconds": -1.0}}),
        ("assistant", {"blocks": [], "response_metrics": {"tokens_per_second": -1.0}}),
        (
            "assistant",
            {"blocks": [], "response_metrics": {"ttft_seconds": float("nan")}},
        ),
        (
            "assistant",
            {"blocks": [], "response_metrics": {"tokens_per_second": "fast"}},
        ),
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


def test_read_rejects_invalid_payload(tmp_path: Path) -> None:
    log = SessionLog.create(_sessions(tmp_path), session_id="invalid-data")
    records = [
        json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()
    ]
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
        '{"id":"broken"}\n',
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
