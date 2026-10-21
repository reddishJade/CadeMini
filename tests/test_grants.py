"""Cade 会话 grants 的回归测试。"""

from __future__ import annotations

from cade.core.permissions import ApprovalRequest
from cade.core.types import JsonValue, ToolCallBlock
from cade.safety.approval import SessionApprover
from cade.safety.grants import GrantStore


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


def test_sensitive_reason_never_adds_a_grant() -> None:
    store = GrantStore()
    approver = SessionApprover(
        grants=store,
        is_tty=lambda: True,
        ask=lambda _: "a",
    )
    command = _call("bash", command="cat .env")

    result = approver(
        ApprovalRequest(command, "The command may access sensitive data.")
    )
    assert result.allowed is True
    assert store.match(command) is False


def test_sensitive_request_does_not_reuse_existing_grant() -> None:
    store = GrantStore()
    command = _call("bash", command="cat .env")
    store.add(command)
    approver = SessionApprover(grants=store, is_tty=lambda: True, ask=lambda _: "n")
    result = approver(
        ApprovalRequest(command, "The command may access sensitive data.")
    )
    assert result.allowed is False


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
