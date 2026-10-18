"""Cade Mini 的显式换窗：把当前任务之前的历史整体遮蔽。"""

from __future__ import annotations

from cade_mini.core.log import SessionLog


def reset_window(
    log: SessionLog,
    *,
    handoff: str | None = None,
    reason: str = "manual",
) -> bool:
    """在最近用户任务处追加 window 事件；没有用户任务时不写入。"""
    entries = log.branch()
    users = [entry for entry in entries if entry.type == "user"]
    if not users:
        return False
    start_id = users[-1].id
    if (
        entries
        and entries[-1].type == "window"
        and entries[-1].data.get("start_id") == start_id
    ):
        return False
    log.append(
        "window",
        {
            "start_id": start_id,
            "reason": reason,
            "handoff": handoff,
        },
    )
    return True
