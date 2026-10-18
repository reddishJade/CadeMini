"""Cade Mini 模式、规则与 grants 的策略测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.mini import (
    GrantStore,
    RuleConfig,
    ToolCallBlock,
    WorkspacePolicy,
    build_tools,
    filter_tools,
)
from cade.mini.types import JsonValue


def _call(name: str, **args: JsonValue) -> ToolCallBlock:
    return ToolCallBlock(id="c1", name=name, args=args)


def test_plan_hides_write_and_denies_mutation(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path, mode="plan")

    assert policy.check(_call("write", path="a.txt", content="x")) == "deny"
    assert policy.check(_call("edit", path="a.txt", old="a", new="b")) == "deny"
    assert policy.check(_call("read", path="a.txt")) == "allow"
    assert policy.check(_call("bash", command="ls")) == "allow"
    assert policy.check(_call("bash", command="rm a.txt")) == "deny"

    names = {tool.name for tool in filter_tools(build_tools(tmp_path), "plan")}
    assert "read" in names
    assert "bash" in names
    assert "write" not in names
    assert "edit" not in names


def test_build_allows_write_and_asks_for_mutating_shell(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path, mode="build")

    assert policy.check(_call("write", path="a.txt", content="x")) == "allow"
    assert policy.check(_call("bash", command="ls")) == "allow"
    assert policy.check(_call("bash", command="rm a.txt")) == "ask"


def test_act_asks_for_write_and_shell(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path, mode="act")

    assert policy.check(_call("write", path="a.txt", content="x")) == "ask"
    assert policy.check(_call("bash", command="ls")) == "ask"


def test_last_matching_rule_wins(tmp_path: Path) -> None:
    rules = [
        RuleConfig(tool="bash", command_prefix="git", decision="deny"),
        RuleConfig(
            tool="bash",
            command_prefix="git status",
            decision="allow",
        ),
        RuleConfig(tool="write", path_prefix="docs/", decision="allow"),
    ]
    policy = WorkspacePolicy(tmp_path, mode="act", rules=rules)

    assert policy.check(_call("bash", command="git status")) == "allow"
    assert policy.check(_call("bash", command="git push")) == "deny"
    assert policy.check(_call("write", path="docs/a.txt", content="x")) == "allow"
    assert policy.check(_call("write", path="src/a.txt", content="x")) == "ask"


def test_grants_allow_ask_but_not_boundary(tmp_path: Path) -> None:
    grants = GrantStore()
    policy = WorkspacePolicy(tmp_path, mode="act", grants=grants)
    call = _call("write", path="a.txt", content="x")

    assert policy.check(call) == "ask"
    grants.add(call)
    assert policy.check(call) == "allow"

    outside = _call("write", path="../outside.txt", content="x")
    grants.add(outside)
    assert policy.check(outside) == "deny"


def test_read_only_overrides_grants(tmp_path: Path) -> None:
    grants = GrantStore()
    policy = WorkspacePolicy(tmp_path, read_only=True, mode="build", grants=grants)
    call = _call("write", path="a.txt", content="x")
    grants.add(call)

    assert policy.check(call) == "deny"


def test_invalid_mode_is_rejected(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path)

    with pytest.raises(ValueError):
        policy.set_mode("invalid")
