"""本会话内由用户显式批准的 grants。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from cade.harness.session.types import ToolCallBlock


@dataclass(frozen=True)
class Grant:
    """一条会话授权；文件按路径，其余工具按完整参数匹配。"""

    tool: str
    path: str | None = None
    command: str | None = None
    arguments: str | None = None


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
            arguments=(
                None if call.name in {"read", "write", "edit"} else _arguments(call)
            ),
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
            if grant.arguments is not None and grant.arguments != _arguments(call):
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


def _arguments(call: ToolCallBlock) -> str:
    return json.dumps(call.args, sort_keys=True, ensure_ascii=False)
