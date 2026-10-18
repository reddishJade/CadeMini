"""本会话内由用户显式批准的 grants。"""

from __future__ import annotations

from dataclasses import dataclass

from cade.mini.core.types import ToolCallBlock


@dataclass(frozen=True)
class Grant:
    """一条会话级批准；路径或命令为空表示匹配该工具的全部调用。"""

    tool: str
    path: str | None = None
    command: str | None = None


class GrantStore:
    """内存 grants；进程结束即失效。持久规则由配置文件表达。"""

    def __init__(self) -> None:
        self._grants: list[Grant] = []

    def add(self, call: ToolCallBlock) -> Grant:
        path = call.args.get("path")
        command = call.args.get("command")
        grant = Grant(
            tool=call.name,
            path=path if isinstance(path, str) else None,
            command=command if isinstance(command, str) else None,
        )
        self._grants.append(grant)
        return grant

    def match(self, call: ToolCallBlock) -> bool:
        for grant in reversed(self._grants):
            if grant.tool != call.name:
                continue
            if grant.path is not None and call.args.get("path") != grant.path:
                continue
            if grant.command is not None and call.args.get("command") != grant.command:
                continue
            return True
        return False

    def clear(self) -> None:
        self._grants.clear()

    def describe(self) -> list[str]:
        result: list[str] = []
        for grant in self._grants:
            detail = grant.path or grant.command or "all"
            result.append(f"{grant.tool}: {detail}")
        return result
