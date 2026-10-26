"""用独立 Python 程序编排当前会话的工具。"""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
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
    manages_output = True
    description = (
        "Run an async Python function body to chain tools and filter intermediate results. "
        "Provide code and a short description of the operation, shown in the UI. "
        "Top-level await and return work; asyncio is available. "
        "Call await tools.name({...}) or await tools['name']({...}) using the other "
        "tools' declared arguments. Calls run serially, including asyncio.gather. "
        "Successful calls return {'content': str, 'metadata': object or null}; "
        "failed calls raise RuntimeError. Only print output and a JSON return value "
        "reach the model. Each call starts fresh. Direct Python I/O follows the shell "
        "sandbox policy; use tools.write/edit for tracked file changes. "
        "Scripts cannot call run_code. Errors do not undo completed actions. "
        "Set timeout to a positive finite number of seconds to override the default "
        "120-second deadline, including tool and approval waits. There is no fixed maximum. "
        "max_output_tokens sets the estimated output budget (default 10000, four characters "
        "per token). Larger output keeps its start and end and saves full text to a temp file. "
        "Printed text and the JSON return value have a separate hard limit of 16 MiB."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "minLength": 1},
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": r"\S",
                    "description": "Short, active-voice summary of what the program does, shown in the UI.",
                },
                "max_output_tokens": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Estimated output budget, at four characters per token. Default: "
                        "10000. Longer output keeps its start and end; full text is saved "
                        "to a temporary file. Does not increase the 16 MiB hard output limit."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": (
                        "Total execution deadline in seconds, including nested tools "
                        "and approval waits. Default: 120. No fixed maximum."
                    ),
                },
            },
            "required": ["code", "description"],
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
        max_tokens = call.args.get("max_output_tokens", 10000)
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 0
        ):
            raise ValueError(
                "Invalid max_output_tokens: must be a non-negative integer"
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
        text = result.output
        additions = []
        if result.value is not None:
            additions.append(
                json.dumps(result.value, ensure_ascii=False, allow_nan=False)
            )
        if result.error:
            additions.append(
                f"Script error: {result.error}\nCompleted actions are not undone."
            )
        if additions:
            text += "\n" if text and not text.endswith("\n") else ""
            text += "\n".join(additions)
        text = text or "Script completed (no output)"
        budget = max_tokens * 4
        if len(text) <= budget:
            return ToolResult(text, is_error=result.error is not None)
        path = await asyncio.to_thread(_save_output, text)
        head_chars = budget // 2
        tail_chars = budget - head_chars
        tail = text[-tail_chars:] if tail_chars else ""
        preview = (
            f"{text[:head_chars]}\n[...{len(text) - budget} characters omitted...]\n{tail}"
            f"\n\n[Output truncated. Full output: {path}]"
        )
        return ToolResult(
            preview,
            is_error=result.error is not None,
            metadata={"fullOutputPath": path},
        )


def _save_output(text: str) -> str:
    """把完整文本保存到权限为仅用户可读写的临时文件。"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix="cade-code-",
        suffix=".txt",
        delete=False,
    ) as stream:
        stream.write(text)
        return stream.name
