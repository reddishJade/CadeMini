"""Cade 六个工作区工具与 policy 的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.coding.security.policy import WorkspacePolicy
from cade.coding.tools.base import WorkspaceTool, resolve_workspace_path
from cade.coding.tools.registry import build_tools
from cade.harness.session.types import JsonValue, ToolCallBlock


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _call(name: str, args: dict[str, JsonValue]) -> ToolCallBlock:
    return ToolCallBlock(id="c1", name=name, args=args)


def _tool(tools: tuple[WorkspaceTool, ...], name: str) -> WorkspaceTool:
    return next(tool for tool in tools if tool.name == name)


async def test_read_file_with_offset_and_limit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    read = _tool(build_tools(root), "read")

    result = await read.execute(
        _call("read", {"path": "a.txt", "offset": 2, "limit": 1})
    )

    assert "2: two" in result.content
    assert "1: one" not in result.content


async def test_read_directory(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "a.txt").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    read = _tool(build_tools(root), "read")

    result = await read.execute(_call("read", {"path": "."}))

    assert "a.txt" in result.content
    assert "sub/" in result.content


async def test_read_allows_external_text_but_rejects_binary(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    read = _tool(build_tools(root), "read")

    result = await read.execute(_call("read", {"path": "../secret.txt"}))
    assert "secret" in result.content
    with pytest.raises(ValueError):
        await read.execute(_call("read", {"path": "bin.dat"}))


async def test_write_and_edit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    tools = build_tools(root)
    write = _tool(tools, "write")
    edit = _tool(tools, "edit")
    read = _tool(tools, "read")

    written = await write.execute(
        _call("write", {"path": "src/file.txt", "content": "hello world"})
    )
    assert "Wrote" in written.content
    assert (root / "src/file.txt").read_text(encoding="utf-8") == "hello world"

    edited = await edit.execute(
        _call("edit", {"path": "src/file.txt", "old": "world", "new": "mini"})
    )
    assert "Edited" in edited.content
    assert (root / "src/file.txt").read_text(encoding="utf-8") == "hello mini"

    result = await read.execute(_call("read", {"path": "src/file.txt"}))
    assert "hello mini" in result.content


async def test_edit_requires_unique_match(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "a.txt").write_text("x x", encoding="utf-8")
    edit = _tool(build_tools(root), "edit")

    with pytest.raises(ValueError, match="appears 2 times"):
        await edit.execute(_call("edit", {"path": "a.txt", "old": "x", "new": "y"}))
    with pytest.raises(ValueError, match="not found"):
        await edit.execute(_call("edit", {"path": "a.txt", "old": "z", "new": "y"}))


async def test_glob_skips_blocked_directories(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "src").mkdir()
    (root / "src/a.py").write_text("", encoding="utf-8")
    (root / "src/b.txt").write_text("", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git/hidden.py").write_text("", encoding="utf-8")
    glob = _tool(build_tools(root), "glob")

    result = await glob.execute(_call("glob", {"pattern": "**/*.py"}))

    assert "src/a.py" in result.content
    assert "hidden.py" not in result.content


async def test_grep_finds_matching_lines(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "a.py").write_text("first\nneedle here\n", encoding="utf-8")
    (root / "b.txt").write_text("needle too\n", encoding="utf-8")
    grep = _tool(build_tools(root), "grep")

    result = await grep.execute(_call("grep", {"pattern": "needle", "glob": "*.py"}))

    assert "a.py:2: needle here" in result.content
    assert "b.txt" not in result.content


async def test_bash_runs_command_and_reports_exit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    bash = _tool(build_tools(root), "bash")

    ok = await bash.execute(_call("bash", {"command": "echo hi"}))
    assert "hi" in ok.content
    assert ok.is_error is False

    failed = await bash.execute(_call("bash", {"command": "exit 3"}))
    assert failed.is_error is True
    assert "exit code: 3" in failed.content


async def test_bash_workdir_does_not_define_writable_scope(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    bash = _tool(build_tools(root), "bash")

    result = await bash.execute(_call("bash", {"command": "echo hi", "workdir": ".."}))
    assert not result.is_error


def test_policy_decisions(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    policy = WorkspacePolicy(root)

    assert policy.check(_call("read", {"path": "a.txt"})).decision == "allow"
    assert policy.check(_call("glob", {"pattern": "**/*"})).decision == "allow"
    assert policy.check(_call("write", {"path": "a.txt"})).decision == "allow"
    assert policy.check(_call("bash", {"command": "echo hi"})).decision == "allow"
    assert policy.check(_call("write", {"path": "../a.txt"})).decision == "ask"
    assert policy.check(_call("unknown", {})).decision == "deny"


def test_read_only_policy_denies_mutations(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    policy = WorkspacePolicy(root, read_only=True)

    assert policy.check(_call("read", {"path": "a.txt"})).decision == "allow"
    assert policy.check(_call("write", {"path": "a.txt"})).decision == "deny"
    assert policy.check(_call("bash", {"command": "echo hi"})).decision == "allow"


def test_resolve_workspace_path_boundary(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "a.txt").write_text("", encoding="utf-8")

    assert resolve_workspace_path(root, "a.txt") == (root / "a.txt").resolve()
    assert (
        resolve_workspace_path(root, str(root / "a.txt")) == (root / "a.txt").resolve()
    )
    with pytest.raises(ValueError):
        resolve_workspace_path(root, "../a.txt")
    with pytest.raises(ValueError):
        resolve_workspace_path(root, str(tmp_path / "outside.txt"))
