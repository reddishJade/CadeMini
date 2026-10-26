"""用独立 Python 程序编排当前会话的工具。"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from typing import Any

from cade.coding.tools.base import WorkspaceTool
from cade.harness.cancellation import CancellationToken
from cade.harness.execution.local.python import run_python
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult, current_tool_context, tool_context


class RunCodeTool(WorkspaceTool):
    """每次执行使用新进程；工具参数沿用直接调用的声明。"""

    name = "run_code"
    handles_cancellation = False
    manages_timeout = True
    description = (
        "Run an async Python function body to chain tools and filter intermediate results. "
        "Top-level await and return work; asyncio is available. "
        "Call await tools.name({...}) or await tools['name']({...}) using the other "
        "tools' declared arguments. Calls run serially, including asyncio.gather. "
        "Successful calls return {'content': str, 'metadata': object or null}; "
        "failed calls raise RuntimeError. Only print output and a JSON return value "
        "reach the model. Each call starts fresh. Direct Python I/O follows the shell "
        "sandbox policy; use tools.write/edit for tracked file changes. "
        "Scripts cannot call run_code. Errors do not undo completed actions. "
        "Set timeout to a positive finite number of seconds to override the default "
        "120-second deadline, including tool and approval waits. There is no fixed maximum."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "minLength": 1},
                "timeout": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": (
                        "Total execution deadline in seconds, including nested tools "
                        "and approval waits. Default: 120. No fixed maximum."
                    ),
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        }

    async def execute(
        self, call: ToolCallBlock, signal: CancellationToken | None = None
    ) -> ToolResult:
        runtime = current_tool_context()
        invoke = runtime.execute_tool
        if invoke is None:
            raise RuntimeError("run_code requires the shared tool executor")
        timeout = call.args.get("timeout", runtime.timeout_seconds)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or ("timeout" in call.args and timeout <= 0)
        ):
            raise ValueError(
                "Invalid timeout: must be a positive finite number of seconds"
            )
        with tool_context(replace(runtime, timeout_seconds=timeout)):
            try:
                result = await asyncio.wait_for(
                    run_python(
                        str(call.args["code"]),
                        workspace=self.workspace,
                        sandbox=self.sandbox,
                        names=[
                            tool.name
                            for tool in runtime.tools
                            if tool.name != self.name
                        ],
                        invoke=invoke,
                    ),
                    timeout=timeout if timeout > 0 else None,
                )
            except TimeoutError:
                return ToolResult(
                    f"Script timed out after {timeout:g}s. Completed actions are not undone.",
                    is_error=True,
                )
        parts = [result.output.rstrip()]
        if result.value is not None:
            parts.append(json.dumps(result.value, ensure_ascii=False, allow_nan=False))
        if result.error:
            parts.append(
                f"Script error: {result.error}\nCompleted actions are not undone."
            )
        return ToolResult(
            "\n".join(part for part in parts if part) or "Script completed (no output)",
            is_error=result.error is not None,
        )
