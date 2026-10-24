"""工作区工具与权限策略的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade.coding.security.policy import WorkspacePolicy
from cade.coding.tools.base import WorkspaceTool, resolve_workspace_path
from cade.coding.tools.bash import BashTool
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
    (root / "a.txt").write_bytes(b"one\ntwo\nthree\n")
    read = _tool(build_tools(root), "read")

    result = await read.execute(
        _call("read", {"path": "a.txt", "offset": 2, "limit": 1})
    )

    assert result.content.startswith("two\n")
    assert "offset=3" in result.content


def test_workspace_tool_catalog_uses_shell_for_search(tmp_path: Path) -> None:
    names = {tool.name for tool in build_tools(tmp_path)}
    assert names == {"read", "write", "edit", "bash", "powershell"}
    policy = WorkspacePolicy(tmp_path)
    for name in ("ls", "find", "grep"):
        assert policy.check(_call(name, {"path": "."})).decision == "deny"
    assert policy.check(_call("bash", {"command": "ls"})).decision == "allow"


async def test_read_decodes_bytes_as_utf8(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    read = _tool(build_tools(root), "read")

    result = await read.execute(_call("read", {"path": "../secret.txt"}))
    assert "secret" in result.content
    result = await read.execute(_call("read", {"path": "bin.dat"}))
    assert result.content == "\x00\x01\x02"


async def test_write_and_edit(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    tools = build_tools(root)
    write = _tool(tools, "write")
    edit = _tool(tools, "edit")
    read = _tool(tools, "read")

    written = await write.execute(
        _call("write", {"path": "src/file.txt", "content": "hello world"})
    )
    assert written.content == "Successfully wrote to src/file.txt"
    assert (root / "src/file.txt").read_text(encoding="utf-8") == "hello world"

    edited = await edit.execute(
        _call(
            "edit",
            {
                "path": "src/file.txt",
                "edits": [{"oldText": "world", "newText": "mini"}],
            },
        )
    )
    assert edited.content == "Successfully replaced 1 block(s) in src/file.txt."
    assert (root / "src/file.txt").read_text(encoding="utf-8") == "hello mini"

    result = await read.execute(_call("read", {"path": "src/file.txt"}))
    assert "hello mini" in result.content


async def test_edit_requires_unique_match(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    (root / "a.txt").write_text("x x", encoding="utf-8")
    edit = _tool(build_tools(root), "edit")

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await edit.execute(
            _call(
                "edit", {"path": "a.txt", "edits": [{"oldText": "x", "newText": "y"}]}
            )
        )
    with pytest.raises(ValueError, match="Could not find"):
        await edit.execute(
            _call(
                "edit", {"path": "a.txt", "edits": [{"oldText": "z", "newText": "y"}]}
            )
        )


async def test_shell_runs_command_and_reports_exit(
    tmp_path: Path, shell_tool_class: type[BashTool]
) -> None:
    root = _workspace(tmp_path)
    shell = _tool(build_tools(root), shell_tool_class.name)

    ok = await shell.execute(_call(shell.name, {"command": "echo hi"}))
    assert "hi" in ok.content
    assert ok.is_error is False

    failed = await shell.execute(_call(shell.name, {"command": "exit 3"}))
    assert failed.is_error is True
    assert "Command exited with code 3" in failed.content


async def test_shell_workdir_does_not_define_writable_scope(
    tmp_path: Path, shell_tool_class: type[BashTool]
) -> None:
    root = _workspace(tmp_path)
    shell = _tool(build_tools(root), shell_tool_class.name)

    result = await shell.execute(
        _call(shell.name, {"command": "echo hi", "workdir": ".."})
    )
    assert not result.is_error


def test_policy_decisions(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    policy = WorkspacePolicy(root)

    assert policy.check(_call("read", {"path": "a.txt"})).decision == "allow"
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
