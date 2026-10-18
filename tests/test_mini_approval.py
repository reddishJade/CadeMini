"""Cade Mini 审批流程的回归测试。"""

from __future__ import annotations

from collections.abc import Iterator

from cade_mini.core.types import ToolCallBlock
from cade_mini.safety.approval import SessionApprover


def _call(name: str = "write") -> ToolCallBlock:
    return ToolCallBlock(
        id="c1",
        name=name,
        args={"path": "a.txt", "content": "x"},
    )


def test_auto_approve() -> None:
    approver = SessionApprover(auto_approve=True, is_tty=lambda: False)

    assert approver(_call()) is True


def test_non_tty_denies_without_prompting() -> None:
    prompts: list[str] = []
    approver = SessionApprover(
        ask=lambda prompt: prompts.append(prompt) or "y",
        is_tty=lambda: False,
    )

    assert approver(_call()) is False
    assert prompts == []


def test_yes_no_and_always_decisions() -> None:
    answers: Iterator[str] = iter(["y", "n", "a", "n"])
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    approver = SessionApprover(ask=ask, is_tty=lambda: True)

    assert approver(_call("write")) is True
    assert approver(_call("write")) is False
    assert approver(_call("write")) is True
    assert approver(_call("write")) is True
    assert len(prompts) == 3

    assert approver(_call("bash")) is False
    assert len(prompts) == 4
