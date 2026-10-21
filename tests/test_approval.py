"""Cade 审批流程的回归测试。"""

from __future__ import annotations

from collections.abc import Iterator

from cade.core.permissions import ApprovalRequest
from cade.core.types import ToolCallBlock
from cade.safety.approval import SessionApprover


def _call(name: str = "write") -> ApprovalRequest:
    return ApprovalRequest(
        ToolCallBlock(
            id="c1",
            name=name,
            args={"path": "a.txt", "content": "x"},
        ),
        "This action changes files.",
    )


def test_auto_approve() -> None:
    approver = SessionApprover(auto_approve=True, is_tty=lambda: False)

    assert approver(_call()).allowed is True


def test_non_tty_denies_without_prompting() -> None:
    prompts: list[str] = []
    approver = SessionApprover(
        ask=lambda prompt: prompts.append(prompt) or "y",
        is_tty=lambda: False,
    )

    assert approver(_call()).allowed is False
    assert prompts == []


def test_yes_no_and_always_decisions() -> None:
    answers: Iterator[str] = iter(["y", "n", "a", "n"])
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    approver = SessionApprover(ask=ask, is_tty=lambda: True)

    assert approver(_call("write")).allowed is True
    assert approver(_call("write")).allowed is False
    assert approver(_call("write")).allowed is True
    assert approver(_call("write")).allowed is True
    assert len(prompts) == 3

    assert approver(_call("bash")).allowed is False
    assert len(prompts) == 4
