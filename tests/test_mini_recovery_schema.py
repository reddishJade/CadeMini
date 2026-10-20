"""恢复事实的引用完整性。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.core.log import SessionLog
from cade_mini.core.types import LogError


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
