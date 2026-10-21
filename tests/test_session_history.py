"""会话树导航、独立副本和旧日志恢复的回归测试。"""

from __future__ import annotations

import json
from pathlib import Path
from random import Random

import pytest

from cade.core.history import history_tree
from cade.core.log import SessionLog, list_sessions
from cade.core.projection import project
from cade.core.types import LogEntry, LogError, UserMessage


def _session(tmp_path: Path) -> tuple[SessionLog, list[LogEntry]]:
    log = SessionLog.create(tmp_path / "sessions", project_root=tmp_path)
    log.append("system", {"text": "sys"})
    first = log.append("user", {"text": "first task"})
    answer = log.append(
        "assistant", {"blocks": [{"type": "text", "text": "first answer"}]}
    )
    second = log.append("user", {"text": "second task"})
    last = log.append(
        "assistant", {"blocks": [{"type": "text", "text": "second answer"}]}
    )
    return log, [first, answer, second, last]


def _users(log: SessionLog) -> list[str]:
    return [
        message.text
        for message in project(log.branch())
        if isinstance(message, UserMessage)
    ]


def test_tree_switch_preserves_all_branches_and_reopens_selected_input(
    tmp_path: Path,
) -> None:
    log, turns = _session(tmp_path)
    original = log.path.read_bytes()
    navigation = log.switch_branch(turns[2].id)
    assert log.path.read_bytes().startswith(original)
    assert navigation.parent == turns[2].parent
    assert log.navigation_draft == "second task"
    assert _users(log) == ["first task"]
    reopened = SessionLog.open(log.path)
    assert reopened.session_id == log.session_id
    assert reopened.navigation_draft == "second task"
    assert _users(reopened) == ["first task"]
    alternative = reopened.append("user", {"text": "alternative task"})
    assert reopened.navigation_draft is None
    assert _users(reopened) == ["first task", "alternative task"]
    assert turns[2].id not in {e.id for e in reopened.branch()}
    assert turns[2].id in {e.id for e in reopened.entries()}
    nodes = history_tree(reopened.entries())
    assert [node.entry.id for node in nodes] == [e.id for e in turns] + [alternative.id]
    assert nodes[-1].depth == nodes[2].depth
    assert nodes[-1].active and nodes[-1].current
    assert not nodes[2].active and not nodes[3].active
    reopened.switch_branch(turns[3].id)
    assert reopened.navigation_draft == ""
    assert _users(SessionLog.open(log.path)) == ["first task", "second task"]
    assert len(list((tmp_path / "sessions").glob("session-*.jsonl"))) == 1


def test_navigation_before_first_user_keeps_session_resumable(tmp_path: Path) -> None:
    log, turns = _session(tmp_path)
    log.switch_branch(turns[0].id)
    assert _users(log) == []
    assert log.navigation_draft == "first task"
    assert not any(node.current for node in history_tree(log.entries()))
    sessions = list_sessions(tmp_path / "sessions")
    assert [info.id for info in sessions] == [log.session_id]
    assert sessions[0].title == "first task"
    assert SessionLog.open(log.path).navigation_draft == "first task"


def test_repeated_navigation_matches_independent_path_model(tmp_path: Path) -> None:
    log = SessionLog.create(tmp_path / "sessions")
    log.append("system", {"text": "sys"})
    random = Random(17)
    current: tuple[str, ...] = ()
    before: dict[str, tuple[str, ...]] = {}
    for index in range(160):
        if not before or random.random() < 0.65:
            text = f"task {index}"
            entry = log.append("user", {"text": text})
            before[entry.id] = current
            current = (*current, text)
        else:
            selected = random.choice(list(before))
            log.switch_branch(selected)
            current = before[selected]
        assert _users(SessionLog.open(log.path)) == list(current)
        if index % 19 == 0:
            clone = log.copy_branch(tmp_path / "copies", deferred=True)
            assert _users(clone) == list(current)
    assert {e.id for e in log.entries() if e.type == "user"} == set(before)
    assert {node.entry.id for node in history_tree(log.entries())} == set(before)


def test_stale_log_instance_navigates_using_committed_state(tmp_path: Path) -> None:
    log, turns = _session(tmp_path)
    stale = SessionLog.open(log.path)
    log.switch_branch(turns[2].id)
    alternate = log.append("user", {"text": "alternative"})
    stale.switch_branch(turns[1].id)
    assert alternate.id in {e.id for e in stale.entries()}
    assert _users(stale) == ["first task"]
    assert stale.branch()[-1].parent == turns[1].id
    assert SessionLog.open(log.path).head_id == stale.head_id


def test_fork_can_extract_an_inactive_branch_without_switching_source(
    tmp_path: Path,
) -> None:
    log, turns = _session(tmp_path)
    log.switch_branch(turns[0].id)
    log.append("user", {"text": "different root"})
    original = log.path.read_bytes()
    fork = log.copy_branch(
        tmp_path / "sessions", before_entry_id=turns[2].id, deferred=True
    )
    assert fork.session_id != log.session_id
    assert _users(fork) == ["first task"]
    assert not any(e.type == "branch" for e in fork.entries())
    assert {e.id for e in fork.entries()}.isdisjoint(e.id for e in log.entries())
    assert log.path.read_bytes() == original
    assert _users(log) == ["different root"]
    fork.append("user", {"text": "independent continuation"})
    assert _users(SessionLog.open(fork.path)) == [
        "first task",
        "independent continuation",
    ]
    assert log.path.read_bytes() == original


def test_clone_copies_only_active_path_and_remaps_context_cuts(tmp_path: Path) -> None:
    log, turns = _session(tmp_path)
    log.switch_branch(turns[2].id)
    retained = log.append("user", {"text": "alternative"})
    log.append(
        "window", {"start_id": retained.id, "reason": "manual", "handoff": "handoff"}
    )
    log.append(
        "compaction",
        {
            "summary": "summary",
            "first_kept_id": retained.id,
            "tokens_before": 100,
            "model": "test",
            "provider": "test",
        },
    )
    original = log.path.read_bytes()
    clone = log.copy_branch(tmp_path / "sessions", deferred=True)
    users = [e for e in clone.branch() if e.type == "user"]
    assert [e.data["text"] for e in users] == ["first task", "alternative"]
    for event_type, field in (("window", "start_id"), ("compaction", "first_kept_id")):
        entry = next(e for e in clone.branch() if e.type == event_type)
        assert entry.data[field] == users[-1].id
    assert [type(m) for m in project(clone.branch())] == [
        type(m) for m in project(log.branch())
    ]
    assert _users(clone) == ["alternative"]
    assert not any(e.type == "branch" for e in clone.entries())
    assert not any(e.data.get("text") == "second task" for e in clone.entries())
    assert log.path.read_bytes() == original
    assert {e.id for e in clone.entries()}.isdisjoint(e.id for e in log.entries())


def test_navigation_and_copy_preserve_records_and_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "session-original.jsonl"
    path.parent.mkdir()
    records = [
        {
            "id": "old-user",
            "parent": None,
            "ts": "2026-10-18T00:00:00Z",
            "type": "user",
            "data": {"text": "legacy task"},
        },
        {
            "id": "old-answer",
            "parent": "old-user",
            "ts": "2026-10-18T00:00:01Z",
            "type": "assistant",
            "data": {"blocks": [{"type": "text", "text": "legacy answer"}]},
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    original = path.read_bytes()
    log = SessionLog.open(path)
    log.switch_branch("old-user")
    assert path.read_bytes().startswith(original)
    assert log.entries()[-1].parent is None
    assert log.navigation_draft == "legacy task"
    assert _users(log) == []
    assert SessionLog.open(path).navigation_draft == "legacy task"
    log.switch_branch("old-answer")
    assert _users(log) == ["legacy task"]
    assert log.navigation_draft == ""
    clone = log.copy_branch(tmp_path / "sessions")
    copied = [e for e in clone.entries() if e.type != "meta"]
    assert [e.ts for e in copied] == [record["ts"] for record in records]
    info = next(
        info
        for info in list_sessions(tmp_path / "sessions")
        if info.id == clone.session_id
    )
    assert info.created_at <= info.updated_at
    assert info.updated_at != copied[-1].ts


def test_invalid_navigation_does_not_repair_tail_or_append_fact(tmp_path: Path) -> None:
    log, _ = _session(tmp_path)
    with log.path.open("ab") as stream:
        stream.write(b'{"incomplete":')
    original = log.path.read_bytes()
    head = log.head_id
    with pytest.raises(LogError, match="entry not found"):
        log.switch_branch("missing")
    assert log.path.read_bytes() == original
    assert log.head_id == head


def test_navigation_and_copy_refuse_active_run_lock(tmp_path: Path) -> None:
    log, turns = _session(tmp_path)
    original = log.path.read_bytes()
    with log.run_lock():
        with pytest.raises(LogError, match="already running"):
            log.switch_branch(turns[0].id)
        with pytest.raises(LogError, match="already running"):
            log.copy_branch(tmp_path / "sessions")
    assert log.path.read_bytes() == original


@pytest.mark.parametrize("damage", ["missing-target", "wrong-parent", "unknown-field"])
def test_read_rejects_invalid_navigation_facts(tmp_path: Path, damage: str) -> None:
    log, turns = _session(tmp_path)
    log.switch_branch(turns[2].id)
    records = [
        json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()
    ]
    if damage == "missing-target":
        records[-1]["data"]["selected_id"] = "missing"
    elif damage == "wrong-parent":
        records[-1]["parent"] = turns[3].id
    else:
        records[-1]["data"]["extra"] = True
    log.path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(LogError):
        SessionLog.open(log.path)
