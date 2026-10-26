"""Cade 审批流程的回归测试。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from cade.coding.security.approval import SessionApprover
from cade.harness.permissions import ApprovalRequest
from cade.harness.session.types import JsonValue, ToolCallBlock
from cade.hosts.tui.approval import full_edit_preview_lines


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


@pytest.mark.parametrize("invalid", [None, 42, "bad", []])
def test_full_edit_preview_skips_invalid_items(invalid: JsonValue) -> None:
    lines = full_edit_preview_lines(
        {
            "path": "a.txt",
            "edits": [invalid, {"oldText": "原文", "newText": "新文"}],
        }
    )
    text = "\n".join(lines)
    assert "Edit 1" not in text
    assert "Edit 2" in text
    assert '"原文"' in text
    assert '"新文"' in text
