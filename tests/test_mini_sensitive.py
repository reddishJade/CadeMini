"""敏感路径识别、审批和外部 Git 目标的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.core.types import JsonValue, ToolCallBlock
from cade_mini.safety.grants import GrantStore
from cade_mini.safety.policy import WorkspacePolicy
from cade_mini.safety.sensitive import (
    contains_sensitive_path,
    is_sensitive_path,
)
from cade_mini.safety.shell import classify_command, risk_reason
from cade_mini.tools.workspace import build_tools


def _call(name: str, **args: JsonValue) -> ToolCallBlock:
    return ToolCallBlock(id="c1", name=name, args=args)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def test_sensitive_path_detection(tmp_path: Path, home: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert is_sensitive_path(home / ".ssh" / "id_rsa", home)
    assert is_sensitive_path(home / ".aws" / "credentials", home)
    assert is_sensitive_path(home / ".config" / "gh" / "hosts.yml", home)
    assert is_sensitive_path(workspace / ".env", home)
    assert is_sensitive_path(workspace / ".env.local", home)
    assert not is_sensitive_path(workspace / ".env.example", home)
    assert contains_sensitive_path(home, workspace, home)


def test_sensitive_paths_require_approval(tmp_path: Path, home: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspacePolicy(workspace)

    read_env = policy.check(_call("read", path=".env"))
    assert read_env.decision == "ask"
    assert "sensitive" in read_env.reason.lower()

    read_key = policy.check(_call("read", path=str(home / ".ssh" / "id_rsa")))
    assert read_key.decision == "ask"
    assert "sensitive" in read_key.reason.lower()

    write_env = policy.check(_call("write", path=".env", content="TOKEN=1"))
    assert write_env.decision == "ask"

    assert policy.check(_call("read", path=".env.example")).decision == "allow"
    assert policy.check(_call("read", path=".git/config")).decision == "deny"


def test_plan_still_denies_sensitive_writes(tmp_path: Path, home: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspacePolicy(workspace, mode="plan")
    assert (
        policy.check(_call("write", path=".env", content="TOKEN=1")).decision == "deny"
    )
    assert policy.check(_call("read", path=".env")).decision == "ask"
    assert policy.check(_call("bash", command="cat .env")).decision == "ask"


def test_shell_sensitive_reads_and_external_git_request_approval(
    tmp_path: Path, home: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    assert risk_reason("cat .env", workspace) is not None
    assert risk_reason("cat ~/.ssh/id_rsa", workspace) is not None
    assert risk_reason("echo hi > .env", workspace) is not None
    assert risk_reason("bash -c 'cat .env'", workspace) is not None
    key = home / ".ssh" / "id_rsa"
    assert risk_reason(f"python -c \"open('{key}')\"", workspace) is not None
    assert risk_reason("cat .env.example", workspace) is None
    assert risk_reason("cat /etc/hosts", workspace) is None

    assert risk_reason(f"git -C {outside} branch -D old", workspace) is not None
    assert risk_reason(f"git -C {outside} status", workspace) is None
    assert classify_command(f"git -C {outside} status") == "read_only"
    assert classify_command(f"git -C {outside} branch -D old") == "mutating"


def test_policy_asks_for_shell_sensitive_access(tmp_path: Path, home: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = WorkspacePolicy(workspace)

    assert policy.check(_call("bash", command="cat .env")).decision == "ask"
    check = policy.check(_call("bash", command="cat ~/.ssh/id_rsa"))
    assert check.decision == "ask"
    assert "sensitive" in check.reason.lower()

    check = policy.check(_call("bash", command=f"git -C {outside} branch -D old"))
    assert check.decision == "ask"
    assert "external Git" in check.reason

    assert (
        policy.check(_call("bash", command=f"git -C {outside} status")).decision
        == "allow"
    )


def test_sensitive_scope_is_explicit(tmp_path: Path, home: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspacePolicy(workspace)
    target = home / ".ssh" / "id_rsa"
    check = policy.check(_call("write", path=str(target), content="key"))
    assert check.decision == "ask"
    assert check.scope.writable_paths == (target.resolve(),)

    check = policy.check(
        _call(
            "bash",
            command="python script.py",
            additional_write_paths=[str(home / ".ssh")],
        )
    )
    assert check.decision == "ask"
    assert "sensitive" in check.reason.lower()


def test_sensitive_access_is_not_remembered_by_session_grants(
    tmp_path: Path, home: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = WorkspacePolicy(workspace, grants=GrantStore())
    command = _call("bash", command="cat .env")
    policy.grants.add(command)
    assert policy.check(command).decision == "ask"

    read = _call("read", path=".env")
    policy.grants.add(read)
    assert policy.check(read).decision == "ask"


def test_approved_file_tools_can_access_env_but_glob_still_hides_it(
    tmp_path: Path, home: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = workspace / ".env"
    env.write_text("TOKEN=secret\n", encoding="utf-8")
    tools = {tool.name: tool for tool in build_tools(workspace)}

    assert "TOKEN=secret" in tools["read"].run({"path": ".env"}).content
    assert ".env" not in tools["glob"].run({"pattern": "*"}).content
