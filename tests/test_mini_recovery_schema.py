"""V4 恢复事实的版本边界和引用完整性。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade_mini.core.log import SessionLog
from cade_mini.core.types import LogError


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("event_type", ["checkpoint", "restore"])
def test_older_envelopes_cannot_contain_recovery_events(
    tmp_path: Path, version: int, event_type: str
) -> None:
    log = SessionLog.create(tmp_path)
    user = log.append("user", {"text": "task"})
    if event_type == "checkpoint":
        log.append(
            "checkpoint",
            {
                "checkpoint_id": "test",
                "user_id": user.id,
                "anchor_id": user.parent,
                "phase": "started",
            },
        )
    else:
        log.append(
            "restore",
            {
                "operation_id": "test",
                "target_id": user.id,
                "draft_id": None,
                "scope": "code",
                "kind": "revert",
            },
        )
    records = [
        json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()
    ]
    records[-1]["v"] = version
    log.path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(LogError):
        SessionLog.open(log.path)


@pytest.mark.parametrize("damage", ["anchor", "user", "phase", "extra"])
def test_invalid_checkpoint_is_rejected_before_tail_repair(
    tmp_path: Path, damage: str
) -> None:
    log = SessionLog.create(tmp_path)
    user = log.append("user", {"text": "task"})
    data = {
        "checkpoint_id": "test",
        "user_id": user.id,
        "anchor_id": user.parent,
        "phase": "started",
    }
    if damage == "anchor":
        data["anchor_id"] = "missing"
    elif damage == "user":
        data["user_id"] = user.parent
    elif damage == "phase":
        data["phase"] = "unknown"
    else:
        data["extra"] = "unknown"
    with log.path.open("ab") as stream:
        stream.write(b'{"unfinished":')
    original = log.path.read_bytes()
    with pytest.raises(LogError):
        log.append("checkpoint", data)
    assert log.path.read_bytes() == original


def test_code_only_restore_cannot_trim_conversation(tmp_path: Path) -> None:
    log = SessionLog.create(tmp_path)
    first = log.append("user", {"text": "first"})
    log.append("assistant", {"blocks": [{"type": "text", "text": "answer"}]})
    original = log.path.read_bytes()
    with pytest.raises(LogError, match="Code-only"):
        log.append(
            "restore",
            {
                "operation_id": "test",
                "target_id": first.id,
                "draft_id": None,
                "scope": "code",
                "kind": "revert",
            },
        )
    assert log.path.read_bytes() == original
