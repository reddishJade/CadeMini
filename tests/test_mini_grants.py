"""Cade Mini 会话 grants 的回归测试。"""

from __future__ import annotations

from cade_mini.core.permissions import ApprovalRequest
from cade_mini.core.types import JsonValue, ToolCallBlock
from cade_mini.safety.approval import SessionApprover
from cade_mini.safety.grants import GrantStore


def _call(name: str, **args: JsonValue) -> ToolCallBlock:
    return ToolCallBlock(id="c1", name=name, args=args)


def test_add_match_and_clear() -> None:
    store = GrantStore()
    write = _call("write", path="a.txt", content="x")

    assert store.match(write) is False
    store.add(write)
    assert store.match(write) is True
    assert store.match(_call("write", path="b.txt", content="x")) is False
    assert store.describe() == ["write: a.txt"]

    store.clear()
    assert store.match(write) is False


def test_command_grant_matches_exact_command() -> None:
    store = GrantStore()
    command = _call("bash", command="echo hi")
    store.add(command)

    assert store.match(_call("bash", command="echo hi")) is True
    assert store.match(_call("bash", command="echo bye")) is False


def test_always_adds_a_grant() -> None:
    store = GrantStore()
    approver = SessionApprover(
        grants=store,
        is_tty=lambda: True,
        ask=lambda _: "a",
    )
    command = _call("bash", command="echo hi")

    assert approver(ApprovalRequest(command, "Run command")).allowed is True
    assert store.match(command) is True


def test_command_grant_does_not_expand_workdir_or_write_scope() -> None:
    store = GrantStore()
    store.add(_call("bash", command="python task.py", workdir="src"))
    assert not store.match(_call("bash", command="python task.py", workdir="other"))
    assert not store.match(
        _call(
            "bash",
            command="python task.py",
            workdir="src",
            additional_write_paths=["/outside"],
        )
    )


def test_mcp_grant_matches_arguments_not_every_remote_action() -> None:
    store = GrantStore()
    store.add(_call("mcp__server__send", destination="one"))
    assert not store.match(_call("mcp__server__send", destination="two"))
