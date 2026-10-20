"""将已校验的会话记录投影为完整历史树，不执行 I/O 或修改会话。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cade_mini.core.types import LogEntry

_VISIBLE_TYPES = frozenset({"user", "assistant", "tool_result", "compaction", "window"})


@dataclass(frozen=True)
class HistoryNode:
    """历史选择器的一行；隐藏的审计记录不占展示层级。"""

    entry: LogEntry
    depth: int
    active: bool
    current: bool


def history_tree(entries: Sequence[LogEntry]) -> tuple[HistoryNode, ...]:
    """按父子关系展开全部分支；兄弟节点保持日志中的创建顺序。"""
    if not entries:
        return ()
    by_id = {entry.id: entry for entry in entries}
    active: set[str] = set()
    current: str | None = entries[-1].id
    visible_head: str | None = None
    while current is not None:
        active.add(current)
        entry = by_id[current]
        if visible_head is None and entry.type in _VISIBLE_TYPES:
            visible_head = current
        current = entry.parent

    nearest: dict[str | None, str | None] = {None: None}
    children: dict[str | None, list[LogEntry]] = {}
    for entry in entries:
        parent = nearest[entry.parent]
        if entry.type in _VISIBLE_TYPES:
            children.setdefault(parent, []).append(entry)
            nearest[entry.id] = entry.id
        else:
            nearest[entry.id] = parent

    result: list[HistoryNode] = []
    pending = [(entry, 0) for entry in reversed(children.get(None, []))]
    while pending:
        entry, depth = pending.pop()
        result.append(
            HistoryNode(entry, depth, entry.id in active, entry.id == visible_head)
        )
        pending.extend(
            (child, depth + 1) for child in reversed(children.get(entry.id, []))
        )
    return tuple(result)
