"""工具授权结果和逐调用执行范围；不依赖具体权限策略。"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from cade.harness.execution.scope import ExecutionScope
from cade.harness.session.types import ToolCallBlock

type Decision = Literal["allow", "deny", "ask"]


@dataclass(frozen=True)
class PermissionCheck:
    decision: Decision
    reason: str = ""
    scope: ExecutionScope = field(default_factory=ExecutionScope)


class Policy(Protocol):
    """每次判定都携带理由与执行范围。"""

    def check(self, call: ToolCallBlock) -> PermissionCheck: ...


@dataclass(frozen=True)
class ApprovalRequest:
    call: ToolCallBlock
    reason: str
    context: tuple[str, ...] = ()
    messages: tuple[dict[str, Any], ...] = ()
    parent_conversations: tuple[tuple[dict[str, Any], ...], ...] = ()


_PARENTS: ContextVar[tuple[tuple[dict[str, Any], ...], ...]] = ContextVar(
    "cade_parent_authorization", default=()
)


@contextmanager
def parent_authorization(messages: Sequence[dict[str, Any]]) -> Iterator[None]:
    """子代理复核必须保留父会话的用户限制。"""
    token = _PARENTS.set((*_PARENTS.get(), tuple(messages)))
    try:
        yield
    finally:
        _PARENTS.reset(token)


@dataclass(frozen=True)
class ApprovalResult:
    allowed: bool
    reason: str


type Approver = Callable[[ApprovalRequest], ApprovalResult | Awaitable[ApprovalResult]]


async def authorize(
    call: ToolCallBlock,
    policy: Policy | None,
    approve: Approver | None,
    *,
    context: Sequence[str] = (),
    messages: Sequence[dict[str, Any]] = (),
) -> PermissionCheck:
    """只对 ask 请求授权，保留拒绝理由和本次执行范围。"""
    check = policy.check(call) if policy is not None else PermissionCheck("allow")
    if check.decision != "ask":
        return check
    if approve is None:
        return PermissionCheck(
            "deny",
            f"{check.reason} Approval is required but unavailable.".strip(),
            check.scope,
        )
    request = ApprovalRequest(
        call, check.reason, tuple(context), tuple(messages), _PARENTS.get()
    )
    answer = approve(request)
    if inspect.isawaitable(answer):
        answer = await answer
    return PermissionCheck(
        "allow" if answer.allowed else "deny",
        f"{check.reason} {answer.reason}".strip(),
        check.scope,
    )
