"""Cade Mini 工具生命周期 hooks 的回归测试。"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.mini import (
    Budget,
    HookConfig,
    HookRunner,
    SessionLog,
    ToolCallBlock,
    WorkspacePolicy,
    WriteTool,
    run,
)


class FakeProvider:
    model = "fake"
    transport = "fake"

    def __init__(self, batches: list[list[ProviderEvent]]) -> None:
        self.batches = list(batches)

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del messages, tools, options
        batch = self.batches.pop(0)

        async def events() -> AsyncIterator[ProviderEvent]:
            for event in batch:
                yield event

        return events()


def _text(text: str) -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage(text, "end_turn")]


def _script(path: Path, body: str) -> str:
    path.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def _call() -> ToolCallBlock:
    return ToolCallBlock(
        id="w1",
        name="write",
        args={"path": "a.txt", "content": "x"},
    )


def test_before_hook_can_deny(tmp_path: Path) -> None:
    command = _script(
        tmp_path / "deny.py",
        "import sys\nprint(' blocked ')\nsys.exit(1)\n",
    )
    runner = HookRunner(
        [HookConfig(event="before_tool", command=command, matcher="write")],
        tmp_path,
    )

    allowed, output = asyncio.run(runner.before(_call()))
    assert allowed is False
    assert "blocked" in output

    allowed, output = asyncio.run(
        runner.before(_call().model_copy(update={"name": "read"}))
    )
    assert allowed is True
    assert output == ""


def test_after_hook_runs(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    command = _script(
        tmp_path / "after.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ok')\n",
    )
    runner = HookRunner(
        [HookConfig(event="after_tool", command=command, matcher="write")],
        tmp_path,
    )

    asyncio.run(runner.after(_call(), "result", False))

    assert marker.read_text(encoding="utf-8") == "ok"


async def test_before_hook_denies_tool_in_loop(tmp_path: Path) -> None:
    command = _script(
        tmp_path / "deny.py",
        "import sys\nprint('blocked')\nsys.exit(1)\n",
    )
    runner = HookRunner(
        [HookConfig(event="before_tool", command=command, matcher="write")],
        tmp_path,
    )
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="w1",
                            name="write",
                            input={"path": "a.txt", "content": "x"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("done"),
        ]
    )
    log = SessionLog.create(tmp_path / "sessions")
    log.append("system", {"text": "s"})

    result = await run(
        log,
        "write",
        provider=provider,
        tools=(WriteTool(tmp_path),),
        budget=Budget(100_000),
        policy=WorkspacePolicy(tmp_path, mode="build"),
        hooks=runner,
    )

    assert result.termination == "completed"
    assert not (tmp_path / "a.txt").exists()
    tool_result = next(entry for entry in log.branch() if entry.type == "tool_result")
    assert "Hook denied" in str(tool_result.data["content"])
