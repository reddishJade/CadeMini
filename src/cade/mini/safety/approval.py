"""Cade Mini 的交互式审批：一次、拒绝、或本会话内始终允许。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from cade.mini.core.types import ToolCallBlock
from cade.mini.safety.grants import GrantStore

_PROMPT_LIMIT = 500


class SessionApprover:
    """把 policy 的 ask 决议交给用户；always 记录为会话 grant。"""

    def __init__(
        self,
        *,
        auto_approve: bool = False,
        ask: Callable[[str], str] | None = None,
        is_tty: Callable[[], bool] | None = None,
        grants: GrantStore | None = None,
    ) -> None:
        self.auto_approve = auto_approve
        self._ask = ask or input
        self._is_tty = is_tty or sys.stdin.isatty
        self.grants = grants or GrantStore()

    def __call__(self, call: ToolCallBlock) -> bool:
        if self.auto_approve:
            return True
        if self.grants.match(call):
            return True
        if not self._is_tty():
            return False
        detail = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
        if len(detail) > _PROMPT_LIMIT:
            detail = detail[:_PROMPT_LIMIT] + "..."
        print(
            f"\nApprove {call.name} {detail}? [y]es/[n]o/[a]lways: ",
            file=sys.stderr,
            end="",
            flush=True,
        )
        try:
            answer = self._ask("").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in {"a", "always"}:
            self.grants.add(call)
            return True
        return answer in {"y", "yes"}
