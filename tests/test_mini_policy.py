"""模式、规则冲突和额外写权限的用户行为回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.app.config import RuleConfig
from cade_mini.core.types import JsonValue, ToolCallBlock
from cade_mini.safety.grants import GrantStore
from cade_mini.safety.policy import WorkspacePolicy, filter_tools
from cade_mini.tools.workspace import build_tools


def _call(name: str, **args: JsonValue) -> ToolCallBlock:
    return ToolCallBlock(id="c1", name=name, args=args)


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest",
        "uv run ruff check .",
        "npm run build",
        "ls | head",
        "git status",
    ],
)
def test_build_runs_normal_development_without_approval(
    tmp_path: Path, command: str
) -> None:
    policy = WorkspacePolicy(tmp_path)
    assert policy.check(_call("write", path="a.txt", content="x")).decision == "allow"
    assert policy.check(_call("bash", command=command)).decision == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "rm a.txt",
        "git push --force",
        "git reset --hard",
        "sudo install thing /usr/bin",
        "curl -d @file https://example.com",
    ],
)
def test_build_requires_approval_for_risk(tmp_path: Path, command: str) -> None:
    assert (
        WorkspacePolicy(tmp_path).check(_call("bash", command=command)).decision
        == "ask"
    )


def test_ask_allows_reads_and_requires_approval_for_changes(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path, mode="ask")
    assert policy.check(_call("read", path="a.txt")).decision == "allow"
    assert policy.check(_call("bash", command="ls")).decision == "allow"
    assert policy.check(_call("write", path="a.txt", content="x")).decision == "ask"
    assert policy.check(_call("bash", command="python -m pytest")).decision == "ask"
    assert policy.check(_call("goal", action="set", objective="ship")).decision == "ask"


def test_deny_then_ask_then_allow_regardless_of_rule_order(tmp_path: Path) -> None:
    call = _call("bash", command="git status")
    allow = RuleConfig(tool="bash", decision="allow")
    ask = RuleConfig(tool="bash", command_prefix="git", decision="ask")
    deny = RuleConfig(tool="bash", command_prefix="git status", decision="deny")
    for rules in ((allow, deny), (deny, allow), (allow, ask, deny)):
        assert WorkspacePolicy(tmp_path, rules=rules).check(call).decision == "deny"
    for rules in ((allow, ask), (ask, allow)):
        assert WorkspacePolicy(tmp_path, rules=rules).check(call).decision == "ask"


def test_explicit_allow_answers_default_ask_but_not_deny(tmp_path: Path) -> None:
    call = _call("bash", command="rm a.txt")
    allow = RuleConfig(tool="bash", decision="allow")
    assert (
        WorkspacePolicy(tmp_path, mode="ask", rules=[allow]).check(call).decision
        == "allow"
    )
    grants = GrantStore()
    grants.add(call)
    deny = RuleConfig(tool="bash", decision="deny")
    assert (
        WorkspacePolicy(tmp_path, rules=[deny], grants=grants).check(call).decision
        == "deny"
    )
    assert WorkspacePolicy(tmp_path, grants=grants).check(call).decision == "allow"


def test_path_rule_matches_directory_not_text_prefix(tmp_path: Path) -> None:
    policy = WorkspacePolicy(
        tmp_path,
        mode="ask",
        rules=[RuleConfig(tool="write", path_prefix="docs/", decision="allow")],
    )
    assert policy.check(_call("write", path="docs/a.txt")).decision == "allow"
    assert policy.check(_call("write", path="docs-other/a.txt")).decision == "ask"
    assert policy.check(_call("write", path="docs/../src/a.txt")).decision == "ask"


def test_external_write_requests_exact_scope(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    call = _call("write", path=str(outside), content="x")
    policy = WorkspacePolicy(root)
    check = policy.check(call)
    assert check.decision == "ask"
    assert check.scope.writable_paths == (outside.resolve(),)
    assert policy.check(_call("read", path=str(outside))).decision == "allow"
    policy.grants.add(call)
    assert policy.check(call).decision == "allow"
    assert (
        policy.check(_call("write", path=str(tmp_path / "other.txt"))).decision == "ask"
    )


def test_shell_extra_scope_does_not_follow_workdir(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    policy = WorkspacePolicy(root)
    read = _call("bash", command="ls", workdir=str(tmp_path))
    assert policy.check(read).decision == "allow"
    assert policy.check(read).scope.writable_paths == ()
    write = _call(
        "bash",
        command="python script.py",
        additional_write_paths=[str(tmp_path / "other")],
    )
    assert policy.check(write).decision == "ask"
    assert policy.check(write).scope.writable_paths == ((tmp_path / "other").resolve(),)


def test_plan_and_read_only_constraints_beat_rules_and_grants(tmp_path: Path) -> None:
    call = _call("write", path="a.txt", content="x")
    grants = GrantStore()
    grants.add(call)
    rules = [RuleConfig(tool="*", decision="allow")]
    for policy in (
        WorkspacePolicy(tmp_path, mode="plan", rules=rules, grants=grants),
        WorkspacePolicy(tmp_path, read_only=True, rules=rules, grants=grants),
        WorkspacePolicy(tmp_path, sandbox="read-only", rules=rules, grants=grants),
    ):
        assert policy.check(call).decision == "deny"
        assert policy.check(_call("bash", command="rm a.txt")).decision == "deny"
        assert policy.check(_call("bash", command="ls")).decision == "allow"
        assert (
            policy.check(_call("goal", action="set", objective="change")).decision
            == "deny"
        )
        assert policy.check(_call("memory", text="change")).decision == "deny"
        assert policy.check(_call("mcp__server__tool")).decision == "deny"


def test_plan_exposes_file_tools_only_for_explicit_plan_writes(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path, mode="plan")
    names = {tool.name for tool in filter_tools(build_tools(tmp_path), "plan")}
    assert {"read", "bash", "write", "edit"} <= names
    call = _call("write", path=str(policy.plan_dir / "task.md"), content="# Plan")
    assert policy.check(call).decision == "ask"
    check = policy.check(call)
    assert check.scope.read_only
    assert check.scope.writable_paths == ((policy.plan_dir / "task.md").resolve(),)
    policy.grants.add(call)
    assert policy.check(call).decision == "allow"
    policy.set_mode("build")
    assert policy.check(_call("write", path="a.txt")).decision == "allow"


def test_protected_paths_cannot_be_requested_through_ancestor(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path)
    assert policy.check(_call("write", path=".cade-mini/auth.json")).decision == "deny"
    assert policy.check(_call("write", path=".git/hooks/pre-commit")).decision == "deny"
    assert (
        policy.check(
            _call(
                "bash",
                command="python x.py",
                additional_write_paths=[str(tmp_path.parent)],
            )
        ).decision
        == "deny"
    )


def test_full_access_removes_boundary_not_explicit_deny(tmp_path: Path) -> None:
    outside = _call("write", path=str(tmp_path.parent / "outside.txt"))
    policy = WorkspacePolicy(tmp_path, sandbox="danger-full-access")
    assert policy.check(outside).decision == "allow"
    policy = WorkspacePolicy(
        tmp_path,
        sandbox="danger-full-access",
        rules=[RuleConfig(tool="write", decision="deny")],
    )
    assert policy.check(outside).decision == "deny"


def test_build_reviews_agent_configuration_changes(tmp_path: Path) -> None:
    check = WorkspacePolicy(tmp_path).check(
        _call("edit", path="cade-mini.json", old="ask", new="build")
    )
    assert check.decision == "ask"
    assert "security policy" in check.reason


def test_invalid_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WorkspacePolicy(tmp_path).set_mode("act")
