"""Python 工具编排的真实子进程、权限、日志和取消回归。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cade.ai.events import FinalMessage, ToolCall, ToolCallEvent
from cade.coding.security.policy import Mode, WorkspacePolicy, filter_tools
from cade.coding.security.rules import RuleConfig
from cade.coding.tools.history import HistoryTool
from cade.coding.tools.registry import build_tools
from cade.coding.tools.run_code import RunCodeTool
from cade.coding.workspace.recovery.coordinator import WorkspaceRecovery
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.cancellation import CancellationToken
from cade.harness.execution.local.python import PythonResult
from cade.harness.execution.local.sandbox import PlatformSandbox
from cade.harness.executor import execute_tool, index_tools
from cade.harness.loop import run
from cade.harness.permissions import ApprovalResult, PermissionCheck
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import JsonValue, ToolCallBlock
from cade.harness.tools import ToolResult, current_tool_context
from tests.test_loop import FakeProvider


async def execute(
    root: Path,
    code: str,
    *,
    timeout: float = 5,
    cancel: CancellationToken | None = None,
) -> ToolResult:
    result = await execute_tool(
        ToolCallBlock(
            id="script",
            name="run_code",
            args={"code": code, "description": "Run test program"},
        ),
        index_tools(build_tools(root)),
        timeout_seconds=timeout,
        cancel=cancel,
    )
    return result.result


async def test_chains_real_tools_and_only_returns_selected_data(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha\nsecret detail", encoding="utf-8")
    result = await execute(
        tmp_path,
        "source = await tools.read({'path': 'a.txt'})\n"
        "await tools.write({'path': 'b.txt', 'content': source['content'].upper()})\n"
        "print('处理完成')\nreturn {'lines': len(source['content'].splitlines())}",
    )
    assert not result.is_error, result.content
    assert "处理完成" in result.content
    assert '"lines": 2' in result.content
    assert "secret detail" not in result.content
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "ALPHA\nSECRET DETAIL"


@pytest.mark.parametrize(
    "code",
    [
        "return (",
        "raise ValueError('broken')",
        "raise SystemExit(2)",
        "return object()",
    ],
)
async def test_reports_program_errors(tmp_path: Path, code: str) -> None:
    result = await execute(tmp_path, code)
    assert result.is_error
    assert "Script error" in result.content


async def test_keeps_output_and_effects_before_error(tmp_path: Path) -> None:
    result = await execute(
        tmp_path,
        "await tools.write({'path': 'kept', 'content': 'done'})\n"
        "print('before error', flush=True)\nraise ValueError('broken')",
    )
    assert result.is_error
    assert "before error" in result.content
    assert "broken" in result.content
    assert (tmp_path / "kept").read_text() == "done"


async def test_fresh_state_and_clean_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CADE_TEST_SECRET", "private")
    first = await execute(tmp_path, "global retained\nretained = 42\nreturn retained")
    assert first.content == "42"
    second = await execute(tmp_path, "return retained")
    assert second.is_error and "NameError" in second.content
    env = await execute(
        tmp_path, "import os\nreturn os.environ.get('CADE_TEST_SECRET')"
    )
    assert not env.is_error
    assert "private" not in env.content


@pytest.mark.parametrize(
    "code",
    [
        "return await tools.run_code({'code': 'return 1'})",
        "import sys\nsys.__stdout__.write('[]\\n')\nsys.__stdout__.flush()\nawait asyncio.sleep(10)",
    ],
)
async def test_rejects_recursive_or_invalid_protocol_calls(
    tmp_path: Path, code: str
) -> None:
    result = await execute(tmp_path, code)
    assert result.is_error


@pytest.mark.parametrize("code", ["print('x' * 60000)", "return 'x' * 60000"])
async def test_output_limits_are_explicit_errors(tmp_path: Path, code: str) -> None:
    result = await execute(tmp_path, code)
    assert result.is_error
    assert "50 KiB" in result.content
    assert len(result.content) < 64000


@pytest.mark.parametrize("mode", ["plan", "ask", "build"])
@pytest.mark.parametrize("read_only", [False, True])
def test_program_permissions(tmp_path: Path, mode: Mode, read_only: bool) -> None:
    policy = WorkspacePolicy(tmp_path, mode=mode, read_only=read_only)
    check = policy.check(
        ToolCallBlock(
            id="p",
            name="run_code",
            args={"code": "return 1", "description": "Return a number"},
        )
    )
    expected = (
        "deny" if read_only or mode == "plan" else "ask" if mode == "ask" else "allow"
    )
    assert check.decision == expected
    if mode == "plan":
        assert "run_code" not in {
            tool.name for tool in filter_tools(build_tools(tmp_path), policy.mode)
        }


async def test_nested_denial_and_invalid_arguments_are_catchable(
    tmp_path: Path,
) -> None:
    policy = WorkspacePolicy(
        tmp_path, rules=(RuleConfig(tool="write", decision="deny"),)
    )
    result = await execute_tool(
        ToolCallBlock(
            id="p",
            name="run_code",
            args={
                "description": "Handle rejected tool calls",
                "code": (
                    "try:\n    await tools.write({'path': 'denied', 'content': 'x'})\n"
                    "except RuntimeError as error:\n    print(str(error))\n"
                    "try:\n    await tools.read({'path': 'missing', 'offset': 'bad'})\n"
                    "except RuntimeError as error:\n    print(str(error))\n"
                    "return 'recovered'"
                ),
            },
        ),
        index_tools(build_tools(tmp_path)),
        policy=policy,
    )
    assert not result.result.is_error, result.result.content
    assert "Permission denied" in result.result.content
    assert "schema error" in result.result.content
    assert "recovered" in result.result.content
    assert not (tmp_path / "denied").exists()


@pytest.mark.parametrize("trigger", ["timeout", "cancel"])
async def test_stops_busy_program_before_returning(
    tmp_path: Path, trigger: str
) -> None:
    token = CancellationToken()
    task = asyncio.create_task(
        execute(
            tmp_path,
            "from pathlib import Path\nPath('started').touch()\nwhile True: pass",
            timeout=0.5 if trigger == "timeout" else 5,
            cancel=token,
        )
    )
    for _ in range(100):
        if (tmp_path / "started").exists():
            break
        await asyncio.sleep(0.01)
    assert (tmp_path / "started").exists()
    if trigger == "cancel":
        token.cancel()
    result = await asyncio.wait_for(task, timeout=5)
    assert result.is_error
    assert (
        "timed out" if trigger == "timeout" else "aborted"
    ) in result.content.lower()


@pytest.mark.parametrize("trigger", ["cancel", "timeout"])
async def test_cancellation_drains_nested_tool_and_records_interruption(
    tmp_path: Path,
    trigger: str,
) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    class WaitingTool:
        name = "wait"
        description = "Wait until cancelled"
        parameters: ClassVar[dict[str, Any]] = {"type": "object"}

        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None
        ) -> ToolResult:
            started.set()
            try:
                await asyncio.sleep(30)
                return ToolResult("unexpected")
            finally:
                stopped.set()

    entries: list[dict[str, JsonValue]] = []
    token = CancellationToken()
    task = asyncio.create_task(
        execute_tool(
            ToolCallBlock(
                id="p",
                name="run_code",
                args={
                    "description": "Wait for a tool",
                    "code": "await tools.wait({})",
                    "timeout": 1 if trigger == "timeout" else 600,
                },
            ),
            {"run_code": RunCodeTool(tmp_path), "wait": WaitingTool()},
            cancel=token,
            on_nested=entries.append,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    if trigger == "cancel":
        token.cancel()
    result = await asyncio.wait_for(task, timeout=5)
    assert result.result.is_error
    assert (
        "timed out" if trigger == "timeout" else "aborted"
    ) in result.result.content.lower()
    assert stopped.is_set()
    assert entries[0]["status"] == "started"
    assert entries[-1]["status"] in {"interrupted", "error"}


async def test_loop_logs_nested_calls_without_projecting_them(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hidden intermediate", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="p",
                            name="run_code",
                            input={
                                "description": "Count file characters",
                                "code": "result = await tools.read({'path': 'a.txt'})\nreturn len(result['content'])",
                            },
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            [FinalMessage("done", "end_turn")],
        ]
    )
    log = SessionLog.create(tmp_path / "sessions")
    result = await run(
        log,
        "count",
        provider=provider,
        tools=build_tools(tmp_path),
        budget=Budget(100000),
    )
    assert result.termination == "completed"
    audit = [entry for entry in log.branch() if entry.type == "tool_execution"]
    assert [entry.data["status"] for entry in audit] == ["started", "ok"]
    assert audit[1].data["content"] == "hidden intermediate"
    assert "hidden intermediate" not in json.dumps(provider.requests[1][0])
    assert project(SessionLog.open(log.path).branch()) == project(log.branch())
    history = HistoryTool(tmp_path, log).run({"call_id": "p/1"})
    assert "hidden intermediate" in history.content


async def test_nested_approval_uses_parent_context(tmp_path: Path) -> None:
    requests = []

    class Policy:
        def check(self, call: ToolCallBlock) -> PermissionCheck:
            return PermissionCheck("ask" if call.name == "write" else "allow", "test")

    result = await execute_tool(
        ToolCallBlock(
            id="p",
            name="run_code",
            args={
                "code": "await tools.write({'path': 'approved', 'content': 'ok'})",
                "description": "Write an approved file",
            },
        ),
        index_tools(build_tools(tmp_path)),
        policy=Policy(),
        approve=lambda request: (
            requests.append(request),
            ApprovalResult(True, "approved"),
        )[1],
        messages=({"role": "user", "content": "only this task"},),
    )
    assert not result.result.is_error
    assert len(requests) == 1
    assert requests[0].call.id == "p/1"
    assert requests[0].parent_conversations


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="Native OS sandbox")
async def test_direct_python_io_uses_real_sandbox(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    temp = tmp_path / "private"
    temp.mkdir()
    sandbox = PlatformSandbox(workspace, temp, network=False)
    tools = index_tools(build_tools(workspace, sandbox=sandbox))
    inside = await execute_tool(
        ToolCallBlock(
            id="inside",
            name="run_code",
            args={
                "description": "Write inside the workspace",
                "code": "from pathlib import Path\nPath('inside').write_text('ok')\nreturn 'done'",
            },
        ),
        tools,
    )
    assert not inside.result.is_error, inside.result.content
    assert (workspace / "inside").read_text() == "ok"
    target = tmp_path / "outside"
    outside = await execute_tool(
        ToolCallBlock(
            id="outside",
            name="run_code",
            args={
                "description": "Attempt an outside write",
                "code": f"from pathlib import Path\nPath({str(target)!r}).write_text('denied')",
            },
        ),
        tools,
    )
    assert outside.result.is_error
    assert not target.exists()


async def test_nested_write_participates_in_undo(tmp_path: Path) -> None:
    snapshots = SnapshotStore(tmp_path)
    recovery = WorkspaceRecovery(snapshots)
    log = SessionLog.create(tmp_path / ".cade" / "sessions")
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="p",
                            name="run_code",
                            input={
                                "description": "Write a tracked file",
                                "code": "await tools.write({'path': 'tracked', 'content': 'changed'})",
                            },
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            [FinalMessage("done", "end_turn")],
        ]
    )
    result = await run(
        log,
        "write",
        provider=provider,
        tools=build_tools(tmp_path, snapshots=snapshots),
        budget=Budget(100000),
        lifecycle=recovery,
    )
    assert result.termination == "completed"
    assert (tmp_path / "tracked").read_text() == "changed"
    recovery.step(log, "undo")
    assert not (tmp_path / "tracked").exists()


@pytest.mark.parametrize("override", [None, 600])
async def test_timeout_default_and_override_are_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: int | None,
) -> None:
    async def report_timeout(*args: Any, **kwargs: Any) -> PythonResult:
        return PythonResult("", current_tool_context().timeout_seconds)

    monkeypatch.setattr("cade.coding.tools.run_code.run_python", report_timeout)
    result = await execute_tool(
        ToolCallBlock(
            id="p",
            name="run_code",
            args={
                "code": "return 1",
                "description": "Report the timeout",
                "timeout": override,
            },
        ),
        {"run_code": RunCodeTool(tmp_path)},
    )
    assert not result.result.is_error
    assert json.loads(result.result.content) == (120 if override is None else override)


async def test_override_extends_program_and_nested_tool_deadlines(
    tmp_path: Path,
) -> None:
    class SlowTool:
        name = "slow"
        description = "Return after a short delay"
        parameters: ClassVar[dict[str, Any]] = {"type": "object"}

        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None
        ) -> ToolResult:
            await asyncio.sleep(0.05)
            return ToolResult(str(current_tool_context().timeout_seconds))

    result = await execute_tool(
        ToolCallBlock(
            id="p",
            name="run_code",
            args={
                "description": "Run a slow tool",
                "code": "await asyncio.sleep(0.05)\nreturn (await tools.slow({}))['content']",
                "timeout": 2,
            },
        ),
        {"run_code": RunCodeTool(tmp_path), "slow": SlowTool()},
        timeout_seconds=0.01,
    )
    assert not result.result.is_error, result.result.content
    assert float(json.loads(result.result.content)) == 2


@pytest.mark.parametrize("timeout", [0, -1, True, "slow", float("inf"), float("nan")])
async def test_invalid_timeout_never_starts_program(
    tmp_path: Path, timeout: Any
) -> None:
    result = await execute_tool(
        ToolCallBlock(
            id="p",
            name="run_code",
            args={
                "description": "Test timeout validation",
                "code": "from pathlib import Path\nPath('started').touch()",
                "timeout": timeout,
            },
        ),
        {"run_code": RunCodeTool(tmp_path)},
    )
    assert result.result.is_error
    assert not (tmp_path / "started").exists()


async def test_explicit_timeout_includes_nested_approval_wait(tmp_path: Path) -> None:
    started, stopped = asyncio.Event(), asyncio.Event()

    class Policy:
        def check(self, call: ToolCallBlock) -> PermissionCheck:
            return PermissionCheck("ask" if call.name == "write" else "allow")

    async def approve(request: Any) -> ApprovalResult:
        started.set()
        try:
            await asyncio.sleep(30)
            return ApprovalResult(True, "unexpected")
        finally:
            stopped.set()

    task = asyncio.create_task(
        execute_tool(
            ToolCallBlock(
                id="p",
                name="run_code",
                args={
                    "description": "Wait for write approval",
                    "code": "await tools.write({'path': 'late', 'content': 'x'})",
                    "timeout": 1,
                },
            ),
            index_tools(build_tools(tmp_path)),
            policy=Policy(),
            approve=approve,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    result = await asyncio.wait_for(task, timeout=5)
    assert result.result.is_error
    assert "timed out after 1s" in result.result.content
    assert stopped.is_set()
    assert not (tmp_path / "late").exists()


@pytest.mark.parametrize("description", [None, "", "   ", 42])
async def test_description_is_required_and_nonblank(
    tmp_path: Path, description: Any
) -> None:
    args: dict[str, JsonValue] = {
        "code": "from pathlib import Path\nPath('started').touch()",
    }
    if description is not None:
        args["description"] = description
    result = await execute_tool(
        ToolCallBlock(id="p", name="run_code", args=args),
        {"run_code": RunCodeTool(tmp_path)},
    )
    assert result.result.is_error
    assert "schema error" in result.result.content
    assert not (tmp_path / "started").exists()
