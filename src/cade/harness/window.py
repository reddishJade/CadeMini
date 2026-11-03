"""Cade 的显式换窗：把当前任务之前的历史整体遮蔽。"""

from __future__ import annotations

from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import AssistantMessage, JsonValue, ToolCallBlock


def reset_window(
    log: SessionLog,
    *,
    handoff: str | None = None,
    reason: str = "manual",
    clear_history: bool = False,
) -> bool:
    """追加 window 事件；清空执行历史时，把当前任务和 steering 写入交接。"""
    entries = log.branch()
    users = [
        entry
        for entry in entries
        if entry.type == "user"
        and (not clear_history or entry.data.get("source", "user") in {"user", "goal"})
    ]
    if not users:
        return False
    task = users[-1]
    if clear_history and task.data.get("source") == "goal":
        # 自动续跑消息不应覆盖原任务及此前的 steering。
        last_input = next(i for i, entry in enumerate(entries) if entry.id == task.id)
        previous_run = next(
            (i for i in range(last_input - 1, -1, -1) if entries[i].type == "run"), -1
        )
        roots = [
            entry
            for entry in entries[previous_run + 1 : last_input + 1]
            if entry.type == "user"
            and entry.data.get("source", "user") in {"user", "goal"}
        ]
        task = next(
            (
                entry
                for entry in reversed(roots)
                if entry.data.get("source", "user") == "user"
            ),
            roots[0],
        )
    start_id = task.id
    if (
        entries
        and entries[-1].type == "window"
        and entries[-1].data.get("start_id") == start_id
        and entries[-1].data.get("clear_history", False) == clear_history
    ):
        return False
    data: dict[str, JsonValue] = {
        "start_id": start_id,
        "reason": reason,
        "handoff": handoff,
    }
    if clear_history:
        completed = {
            str(entry.data["call_id"])
            for entry in entries
            if entry.type == "tool_result"
        }
        if any(
            block.id not in completed
            for message in project(entries)
            if isinstance(message, AssistantMessage)
            for block in message.blocks
            if isinstance(block, ToolCallBlock)
        ):
            return False
        task_index = next(i for i, entry in enumerate(entries) if entry.id == start_id)
        instructions = [
            str(entry.data["text"])
            for entry in entries[task_index:]
            if entry.type == "user"
            and (entry.data.get("source") != "goal" or entry.id == start_id)
        ]
        data["handoff"] = "\n\n".join(
            [
                "Continue the current task and preserve all of these user instructions:",
                *instructions,
                *([handoff] if handoff else []),
            ]
        )
        data["clear_history"] = True
    log.append("window", data)
    return True
