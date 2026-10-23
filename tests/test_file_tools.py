"""验证文件、搜索和 shell 工具的行为。"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cade.ai.providers.responses import to_responses_input
from cade.coding.security.policy import WorkspacePolicy
from cade.coding.tools.bash import BashTool
from cade.coding.tools.edit import EditTool
from cade.coding.tools.edit_diff import Edit, apply_edits, diff_details
from cade.coding.tools.read import ReadTool
from cade.coding.tools.registry import build_tools
from cade.coding.tools.truncate import truncate
from cade.harness.events import ToolUpdateEvent
from cade.harness.executor import execute_tool
from cade.harness.request import Budget, build
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolContext, tool_context

_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures/file_tools.json").read_text(encoding="utf-8")
)


async def test_shell_streams_output_and_exposes_current_session(tmp_path: Path) -> None:
    script = "import os,time; print('started',flush=True); time.sleep(.5); print(os.environ['CADE_MODEL'],flush=True)"
    command = (
        shlex.quote(sys.executable.replace("\\", "/")) + " -u -c " + shlex.quote(script)
    )
    updates: list[ToolUpdateEvent] = []
    tool = BashTool(tmp_path)

    def observe(event: object) -> None:
        if isinstance(event, ToolUpdateEvent):
            updates.append(event)

    with tool_context(
        ToolContext("session", "session.jsonl", "provider", "model", "high")
    ):
        pending = asyncio.create_task(
            execute_tool(
                ToolCallBlock(id="b", name="bash", args={"command": command}),
                {"bash": tool},
                on_event=observe,
                timeout_seconds=0.01,
            )
        )
        async with asyncio.timeout(5):
            while not any("started" in event.content for event in updates):
                await asyncio.sleep(0.01)
        assert not pending.done()
        result = await pending
    assert not result.result.is_error
    assert result.result.content == f"started{os.linesep}model{os.linesep}"


@pytest.mark.parametrize("case", _CORPUS["edit"])
def test_edit_reference_results(case: dict[str, Any]) -> None:
    edits = [Edit(item["oldText"], item["newText"]) for item in case["edits"]]
    if "error" in case["expected"]:
        with pytest.raises(ValueError) as failure:
            apply_edits(case["text"], edits, "file.txt")
        assert str(failure.value) == case["expected"]["error"]
    else:
        after = apply_edits(case["text"], edits, "file.txt")
        assert after == case["expected"]["newContent"]
        assert diff_details("file.txt", case["text"], after) == {
            key: case["expected"][key] for key in ("diff", "patch", "firstChangedLine")
        }


def test_edit_keeps_bom_crlf_and_unchanged_unicode(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_bytes(
        '\ufeffconst first = "a";\r\nuntouched “smart”  \r\nconst second = "b";\r\n'.encode()
    )
    result = EditTool(tmp_path).run(
        {
            "path": "file.txt",
            "edits": [
                {"oldText": 'const first = "a";', "newText": "const first = 1;"},
                {"oldText": 'const second = "b";', "newText": "const second = 2;"},
            ],
        }
    )
    assert result.content == "Successfully replaced 2 block(s) in file.txt."
    assert (
        target.read_bytes()
        == "\ufeffconst first = 1;\r\nuntouched “smart”  \r\nconst second = 2;\r\n".encode()
    )


@pytest.mark.parametrize(
    "edits",
    [
        [
            {"oldText": "one", "newText": "ONE"},
            {"oldText": "missing", "newText": "TWO"},
        ],
        [{"oldText": "one two", "newText": "x"}, {"oldText": "two", "newText": "y"}],
    ],
)
def test_failed_batch_does_not_write(
    tmp_path: Path, edits: list[dict[str, str]]
) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"one two\n")
    with pytest.raises(ValueError):
        EditTool(tmp_path).run({"path": "file.txt", "edits": edits})
    assert path.read_bytes() == b"one two\n"


def test_edit_missing_file_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as failure:
        EditTool(tmp_path).run(
            {"path": "missing.txt", "edits": [{"oldText": "old", "newText": "new"}]}
        )
    assert str(failure.value) == "Could not edit file: missing.txt. Error code: ENOENT."


def test_edit_can_replace_a_single_trailing_space() -> None:
    assert apply_edits("x ", [Edit(" ", "_")], "file.txt") == "x_"


@pytest.mark.parametrize(
    "args",
    [
        {"oldText": "old", "newText": "new"},
        {"edits": {"oldText": "old", "newText": "new"}},
        {"edits": '[{"oldText":"old","newText":"new"}]'},
    ],
)
async def test_edit_argument_preparation(tmp_path: Path, args: dict[str, Any]) -> None:
    (tmp_path / "file.txt").write_bytes(b"old")
    tool = EditTool(tmp_path)
    result = await execute_tool(
        ToolCallBlock(id="e", name="edit", args={"path": "file.txt", **args}),
        {"edit": tool},
    )
    assert not result.result.is_error
    assert (tmp_path / "file.txt").read_bytes() == b"new"


async def test_strict_responses_optional_nulls_use_tool_defaults(
    tmp_path: Path,
) -> None:
    (tmp_path / "file.txt").write_bytes(b"content\n")
    tool = ReadTool(tmp_path)
    result = await execute_tool(
        ToolCallBlock(
            id="r",
            name="read",
            args={"path": "file.txt", "offset": None, "limit": None},
        ),
        {"read": tool},
        policy=WorkspacePolicy(tmp_path),
    )
    assert result.result.content == "content\n"


def test_read_paging_long_lines_and_utf8_limits(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_bytes(b"a\n" * 2001)
    tool = ReadTool(tmp_path)
    result = tool.run({"path": "file.txt"})
    assert (
        result.content
        == "\n".join(["a"] * 2000)
        + "\n\n[Showing lines 1-2000 of 2002. Use offset=2001 to continue.]"
    )
    assert tool.run({"path": "file.txt", "offset": 2001}).content == "a\n"
    path.write_bytes(("中" * 20000).encode())
    result = tool.run({"path": "file.txt"})
    assert (
        result.content
        == "[Line 1 is 58.6KB, exceeds 50.0KB limit. Use bash: sed -n '1p' file.txt | head -c 51200]"
    )
    path.write_bytes(b"")
    assert tool.run({"path": "file.txt"}).content == ""


def test_tail_truncation_has_complete_utf8_characters() -> None:
    result = truncate("head\n" + "中" * 100, tail=True, max_bytes=11)
    assert result.content == "中" * 3
    assert result.lastLinePartial
    assert result.outputBytes == 9


@pytest.mark.parametrize("tool_name", ["find", "grep", "ls"])
async def test_search_cannot_read_cade_state(tmp_path: Path, tool_name: str) -> None:
    (tmp_path / ".cade/snapshots/blobs").mkdir(parents=True)
    (tmp_path / ".cade/snapshots/blobs/secret.txt").write_text(
        "private-marker", encoding="utf-8"
    )
    (tmp_path / "visible.txt").write_text("public-marker", encoding="utf-8")
    tool = next(tool for tool in build_tools(tmp_path) if tool.name == tool_name)
    args = (
        {"pattern": "marker"}
        if tool_name == "grep"
        else {"pattern": "*"}
        if tool_name == "find"
        else {}
    )
    result = await execute_tool(
        ToolCallBlock(id="s", name=tool_name, args=args),
        {tool_name: tool},
        policy=WorkspacePolicy(tmp_path),
    )
    assert not result.result.is_error
    assert "private-marker" not in result.result.content
    assert "secret.txt" not in result.result.content
    assert ".cade" not in result.result.content
    denied = await execute_tool(
        ToolCallBlock(id="s", name=tool_name, args={**args, "path": ".cade"}),
        {tool_name: tool},
        policy=WorkspacePolicy(tmp_path),
    )
    assert denied.result.is_error
    assert "Protected path" in denied.result.content


async def test_grep_literal_context_ignore_and_limit(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_bytes(b"ignored.txt\n")
    (tmp_path / "ignored.txt").write_bytes(b"Needle.*\n")
    (tmp_path / "file.txt").write_bytes(b"before\nNeedle.*\nafter\n")
    tool = next(tool for tool in build_tools(tmp_path) if tool.name == "grep")
    result = await tool.execute(
        ToolCallBlock(
            id="s",
            name="grep",
            args={
                "pattern": "needle.*",
                "ignoreCase": True,
                "literal": True,
                "context": 1,
                "limit": 1,
            },
        )
    )
    assert (
        result.content
        == "file.txt-1- before\nfile.txt:2: Needle.*\nfile.txt-3- after\n\n[1 matches limit reached. Use limit=2 for more, or refine pattern]"
    )


def test_image_survives_log_projection_and_responses_conversion(tmp_path: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(image, format="PNG")
    (tmp_path / "picture.png").write_bytes(image.getvalue())
    result = ReadTool(tmp_path).run({"path": "picture.png"})
    assert result.content == "Read image file [image/png]"
    assert result.metadata is not None
    assert base64.b64decode(result.metadata["images"][0]["data"]) == image.getvalue()
    log = SessionLog.create(tmp_path / "sessions")
    log.append(
        "assistant",
        {
            "blocks": [
                {
                    "type": "tool_call",
                    "id": "r",
                    "name": "read",
                    "args": {"path": "picture.png"},
                }
            ]
        },
    )
    log.append(
        "tool_result",
        {
            "call_id": "r",
            "status": "ok",
            "content": result.content,
            "artifact": {"tool_metadata": result.metadata},
        },
    )
    request = build(
        project(SessionLog.open(log.path).branch()),
        tools=(),
        budget=Budget(context_window=32000),
    )
    converted = to_responses_input(list(request.messages))
    attachment = converted[-1]["output"][1]
    assert attachment["type"] == "input_image"
    assert (
        attachment["image_url"]
        == "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    )
