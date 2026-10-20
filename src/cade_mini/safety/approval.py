"""Cade Mini 的交互式审批：一次、拒绝、或本会话内始终允许。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from cade_mini.core.permissions import ApprovalRequest, ApprovalResult
from cade_mini.safety.grants import GrantStore
from cade_mini.safety.sensitive import is_sensitive_reason

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

    def __call__(self, request: ApprovalRequest) -> ApprovalResult:
        call = request.call
        sensitive = is_sensitive_reason(request.reason)
        if self.auto_approve:
            return ApprovalResult(True, "Approved by --yes.")
        if not sensitive and self.grants.match(call):
            return ApprovalResult(True, "Approved by a session grant.")
        if not self._is_tty():
            return ApprovalResult(False, "Approval requires an interactive terminal.")
        detail = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
        if len(detail) > _PROMPT_LIMIT:
            detail = detail[:_PROMPT_LIMIT] + "..."
        choices = "[y]es/[n]o" if sensitive else "[y]es/[n]o/[a]lways"
        print(
            f"\n{request.reason}\nApprove {call.name} {detail}? {choices}: ",
            file=sys.stderr,
            end="",
            flush=True,
        )
        try:
            answer = self._ask("").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return ApprovalResult(False, "Approval was cancelled.")
        if answer in {"a", "always"} and not sensitive:
            self.grants.add(call)
        allowed = answer in {"y", "yes", "a", "always"}
        return ApprovalResult(
            allowed, "Approved by the user." if allowed else "Rejected by the user."
        )
