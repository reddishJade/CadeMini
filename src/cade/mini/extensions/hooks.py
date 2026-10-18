"""工具生命周期 hooks：before_tool 可否决，after_tool 只观察。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from cade.mini.app.config import HookConfig
from cade.mini.core.types import ToolCallBlock

_RESULT_LIMIT = 4_000


class HookRunner:
    """按配置执行匹配的 hook；before 非零退出即拒绝工具。"""

    def __init__(self, hooks: Sequence[HookConfig], workspace: Path) -> None:
        self.hooks = tuple(hooks)
        self.workspace = workspace.resolve()

    async def before(self, call: ToolCallBlock) -> tuple[bool, str]:
        for hook in self.hooks:
            if hook.event != "before_tool" or not _matches(hook, call.name):
                continue
            code, output = await asyncio.to_thread(self._run, hook, call, "")
            if code != 0:
                return False, output or f"hook exited with {code}"
        return True, ""

    async def after(
        self,
        call: ToolCallBlock,
        content: str,
        is_error: bool,
    ) -> None:
        del is_error
        for hook in self.hooks:
            if hook.event != "after_tool" or not _matches(hook, call.name):
                continue
            await asyncio.to_thread(self._run, hook, call, content)

    def _run(
        self,
        hook: HookConfig,
        call: ToolCallBlock,
        content: str,
    ) -> tuple[int, str]:
        env = os.environ.copy()
        env.update(
            {
                "CADE_HOOK_EVENT": hook.event,
                "CADE_TOOL_NAME": call.name,
                "CADE_TOOL_ARGS": json.dumps(
                    call.args,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "CADE_TOOL_RESULT": content[:_RESULT_LIMIT],
            }
        )
        try:
            completed = subprocess.run(
                hook.command,
                shell=True,
                cwd=self.workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=hook.timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, f"hook timed out after {hook.timeout_ms}ms"
        except OSError as exc:
            return 1, f"hook failed to start: {exc}"
        output = (completed.stdout + completed.stderr).strip()
        return completed.returncode, output


def _matches(hook: HookConfig, tool: str) -> bool:
    if hook.matcher is None or hook.matcher == "*":
        return True
    if hook.matcher.endswith("*"):
        return tool.startswith(hook.matcher[:-1])
    return tool == hook.matcher
