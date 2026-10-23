"""平台沙箱的执行范围、真实文件隔离和网络开关回归。"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from cade.coding.security.policy import WorkspacePolicy
from cade.coding.tools.bash import BashTool, PowerShellTool
from cade.coding.tools.registry import build_tools
from cade.harness.cancellation import CancellationToken
from cade.harness.execution.local.sandbox import PlatformSandbox, SandboxUnavailable
from cade.harness.execution.local.subprocess import SubprocessShell
from cade.harness.execution.result import ExecutionResult
from cade.harness.execution.scope import ExecutionScope, SandboxMode, execution_scope
from cade.harness.permissions import ApprovalResult, authorize
from cade.harness.session.types import ToolCallBlock


def _environment(
    tmp_path: Path, *, mode: SandboxMode = "workspace-write", network: bool = True
) -> PlatformSandbox:
    workspace = tmp_path / "workspace"
    temp = tmp_path / "private-temp"
    workspace.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    return PlatformSandbox(workspace, temp, mode=mode, network=network)


def test_windows_skips_sandbox_without_looking_for_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _environment(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")

    def unexpected_lookup(name: str) -> str:
        raise AssertionError("Windows must not look up sandbox binaries")

    monkeypatch.setattr(shutil, "which", unexpected_lookup)
    wrapped = sandbox.wrap(["program", "argument"], sandbox.workspace)
    assert wrapped.argv == ("program", "argument")


@pytest.mark.parametrize(
    ("platform", "backend"), [("linux", "bwrap"), ("darwin", "sandbox-exec")]
)
def test_missing_backend_never_returns_unconfined_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, backend: str
) -> None:
    sandbox = _environment(tmp_path)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SandboxUnavailable):
        sandbox.wrap(["program"], sandbox.workspace)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_scope_is_per_call_and_network_is_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    sandbox = _environment(tmp_path, network=False)
    outside = tmp_path / "external"
    outside.mkdir()
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    with execution_scope(ExecutionScope(writable_paths=(outside.resolve(),))):
        expanded = sandbox.wrap(["program"], sandbox.workspace).argv
    ordinary = sandbox.wrap(["program"], sandbox.workspace).argv
    rendered = (
        json.dumps(str(outside.resolve()))
        if platform == "darwin"
        else str(outside.resolve())
    )
    assert rendered in " ".join(expanded)
    assert rendered not in " ".join(ordinary)
    if platform == "linux":
        assert "--unshare-net" in expanded
        assert "--unshare-net" in ordinary
    else:
        assert "(deny network*)" in expanded[2]
        assert "(deny network*)" in ordinary[2]


async def test_cancelling_shell_kills_child_before_returning(tmp_path: Path) -> None:
    script = tmp_path / "delayed.py"
    script.write_text(
        "from pathlib import Path\nimport time\n"
        "Path('started.txt').write_text('started')\n"
        "time.sleep(1)\nPath('late.txt').write_text('should not happen')\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        command = f"& '{sys.executable}' '{script}'"
    else:
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    signal = CancellationToken()
    task = asyncio.create_task(
        (
            PowerShellTool(tmp_path) if sys.platform == "win32" else BashTool(tmp_path)
        ).execute(
            ToolCallBlock(id="cancel", name="bash", args={"command": command}),
            signal,
        )
    )
    for _ in range(100):
        if (tmp_path / "started.txt").exists():
            break
        await asyncio.sleep(0.05)
    assert (tmp_path / "started.txt").exists()
    signal.cancel("stop")
    with pytest.raises(ValueError, match="Command aborted"):
        await task
    await asyncio.sleep(1.1)
    assert not (tmp_path / "late.txt").exists()


@pytest.mark.parametrize("trigger", ["cancel", "timeout"])
def test_windows_termination_failure_reports_environment_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trigger: str
) -> None:
    proc = Mock(spec=subprocess.Popen, pid=12345, stdout=None, stderr=None)
    proc.poll.return_value = None
    taskkill = Mock(
        return_value=subprocess.CompletedProcess(
            ["taskkill"], 1, stdout="", stderr="ERROR: Access denied"
        )
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", taskkill)
    monkeypatch.setattr(
        "cade.harness.execution.local.subprocess.start_process", Mock(return_value=proc)
    )
    cancel = threading.Event()
    if trigger == "cancel":
        cancel.set()
    with pytest.raises(OSError, match="taskkill exit 1.*Access denied"):
        SubprocessShell().run(
            ["program"],
            cwd=tmp_path,
            timeout=0 if trigger == "timeout" else 30_000,
            cancel_event=cancel,
        )
    taskkill.assert_called_once()
    proc.wait.assert_not_called()


@pytest.fixture
def native_sandbox(tmp_path: Path) -> PlatformSandbox:
    if sys.platform not in {"linux", "darwin"}:
        pytest.skip("Native sandbox tests require Linux or macOS")
    sandbox = _environment(tmp_path)
    probe = _python(sandbox, "pass")
    assert probe.returncode == 0, f"Native sandbox is unusable: {probe.output}"
    return sandbox


def _python(sandbox: PlatformSandbox, code: str) -> ExecutionResult:
    return SubprocessShell(sandbox).run(
        [sys.executable, "-I", "-c", code],
        cwd=sandbox.workspace,
        env=sandbox.environment(),
    )


def test_native_shell_writes_workspace_and_temp_not_external(
    native_sandbox: PlatformSandbox, tmp_path: Path
) -> None:
    sandbox = native_sandbox
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    result = _python(
        sandbox,
        "from pathlib import Path; import tempfile; Path('inside.txt').write_text('ok'); Path(tempfile.gettempdir(), 'temp.txt').write_text('ok')",
    )
    assert result.returncode == 0, result.output
    assert (sandbox.workspace / "inside.txt").read_text() == "ok"
    assert (sandbox.temp_dir / "temp.txt").read_text() == "ok"
    blocked = _python(
        sandbox,
        f"from pathlib import Path; Path({str(outside)!r}).write_text('changed')",
    )
    assert blocked.returncode != 0
    assert outside.read_text() == "unchanged"
    with execution_scope(ExecutionScope(writable_paths=(outside.resolve(),))):
        approved = _python(
            sandbox,
            f"from pathlib import Path; Path({str(outside)!r}).write_text('approved')",
        )
    assert approved.returncode == 0, approved.output
    assert outside.read_text() == "approved"
    blocked_again = _python(
        sandbox,
        f"from pathlib import Path; Path({str(outside)!r}).write_text('unapproved')",
    )
    assert blocked_again.returncode != 0
    assert outside.read_text() == "approved"


def test_native_hidden_state_and_protected_files(
    native_sandbox: PlatformSandbox, tmp_path: Path
) -> None:
    protected = native_sandbox.workspace / "protected.txt"
    protected.write_text("unchanged")
    private = tmp_path / "private"
    private.mkdir()
    marker = private / "marker.txt"
    marker.write_text("private")
    sandbox = PlatformSandbox(
        native_sandbox.workspace,
        native_sandbox.temp_dir,
        protected_paths=(protected,),
        hidden_paths=(private,),
    )
    changed = _python(
        sandbox, "from pathlib import Path; Path('protected.txt').write_text('bad')"
    )
    assert changed.returncode != 0
    assert protected.read_text() == "unchanged"
    read = _python(
        sandbox, f"from pathlib import Path; print(Path({str(marker)!r}).read_text())"
    )
    assert read.returncode != 0
    assert marker.read_text() == "private"


def test_native_skill_scripts_are_readable_without_exposing_state(
    native_sandbox: PlatformSandbox,
) -> None:
    root = native_sandbox.workspace / ".cade"
    skill = root / "skills" / "check"
    skill.mkdir(parents=True)
    script = skill / "check.py"
    script.write_text("print('SCRIPT_OK')", encoding="utf-8")
    secret = root / "credentials.json"
    secret.write_text("PRIVATE", encoding="utf-8")
    sandbox = PlatformSandbox(
        native_sandbox.workspace,
        native_sandbox.temp_dir,
        hidden_paths=(root,),
        readable_paths=(skill,),
    )
    result = SubprocessShell(sandbox).run(
        [sys.executable, "-I", str(script)],
        cwd=sandbox.workspace,
        env=sandbox.environment(),
    )
    assert result.returncode == 0, result.output
    assert "SCRIPT_OK" in result.output
    hidden = _python(
        sandbox, f"from pathlib import Path; print(Path({str(secret)!r}).read_text())"
    )
    assert hidden.returncode != 0
    changed = _python(
        sandbox,
        f"from pathlib import Path; Path({str(script)!r}).write_text('changed')",
    )
    assert changed.returncode != 0


async def test_native_file_worker_uses_same_boundary_and_exact_approval(
    native_sandbox: PlatformSandbox, tmp_path: Path
) -> None:
    sandbox = native_sandbox
    tools = {
        tool.name: tool for tool in build_tools(sandbox.workspace, sandbox=sandbox)
    }
    policy = WorkspacePolicy(sandbox.workspace, temp_dir=sandbox.temp_dir)
    outside = tmp_path / "new-external.txt"
    call = ToolCallBlock(
        id="w", name="write", args={"path": str(outside), "content": "approved"}
    )
    denied = await authorize(
        call, policy, lambda request: ApprovalResult(False, "No external changes")
    )
    assert denied.decision == "deny"
    assert not outside.exists()
    approved = await authorize(
        call, policy, lambda request: ApprovalResult(True, "Requested file")
    )
    with execution_scope(approved.scope):
        result = await tools["write"].execute(call)
    assert not result.is_error, result.content
    assert outside.read_text() == "approved"
    next_call = ToolCallBlock(
        id="next", name="write", args={"path": str(outside), "content": "unapproved"}
    )
    with pytest.raises(ValueError, match="requires approval"):
        await tools["write"].execute(next_call)
    assert outside.read_text() == "approved"
    assert policy.check(next_call).decision == "ask"


async def test_native_file_worker_preserves_large_json_result(
    native_sandbox: PlatformSandbox,
) -> None:
    sandbox = native_sandbox
    (sandbox.workspace / "quotes.txt").write_text(
        ('"' * 400 + "\n") * 200, encoding="utf-8"
    )
    read = next(
        tool
        for tool in build_tools(sandbox.workspace, sandbox=sandbox)
        if tool.name == "read"
    )
    result = await read.execute(
        ToolCallBlock(id="read", name="read", args={"path": "quotes.txt"})
    )
    assert not result.is_error, result.content
    assert result.content.startswith(('"' * 400 + "\n") * 127)
    assert "Output truncated" in result.content


def test_native_read_only_shell_cannot_write(native_sandbox: PlatformSandbox) -> None:
    sandbox = native_sandbox
    with execution_scope(ExecutionScope(read_only=True)):
        result = _python(
            sandbox, "from pathlib import Path; Path('forbidden.txt').write_text('x')"
        )
    assert result.returncode != 0
    assert not (sandbox.workspace / "forbidden.txt").exists()


def test_native_network_toggle_does_not_change_write_scope(
    native_sandbox: PlatformSandbox, tmp_path: Path
) -> None:
    sandbox = native_sandbox
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        code = f"import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', {port})); print('connected')"
        allowed = _python(sandbox, code)
        assert allowed.returncode == 0, allowed.output
        blocked_sandbox = PlatformSandbox(
            sandbox.workspace, sandbox.temp_dir, network=False
        )
        blocked = _python(blocked_sandbox, code)
        assert blocked.returncode != 0
    target = tmp_path / "outside.txt"
    result = _python(
        sandbox, f"from pathlib import Path; Path({str(target)!r}).write_text('x')"
    )
    assert result.returncode != 0
    assert not target.exists()


async def test_native_plan_file_write_does_not_grant_project_write(
    native_sandbox: PlatformSandbox,
) -> None:
    sandbox = native_sandbox
    tools = {
        tool.name: tool for tool in build_tools(sandbox.workspace, sandbox=sandbox)
    }
    policy = WorkspacePolicy(sandbox.workspace, mode="plan")
    path = policy.plan_dir / "task.md"
    call = ToolCallBlock(
        id="plan", name="write", args={"path": str(path), "content": "# Plan"}
    )
    check = await authorize(
        call, policy, lambda request: ApprovalResult(True, "Write the requested plan")
    )
    with execution_scope(check.scope):
        result = await tools["write"].execute(call)
    assert not result.is_error, result.content
    assert path.read_text() == "# Plan"
    assert (
        policy.check(
            ToolCallBlock(
                id="project", name="write", args={"path": "source.py", "content": "x"}
            )
        ).decision
        == "deny"
    )
