"""独立子代理：在自己的会话中完成一次任务并回传结果。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .history import HistoryTool
from .log import SessionLog
from .loop import CancellationToken, Policy, Provider, Tool, ToolResult, run
from .request import Budget
from .tools import _text_arg
from .types import ToolCallBlock


class SubagentTool:
    """父代理通过 subagent 工具启动一个不递归的子会话。"""

    name = "subagent"
    description = (
        "Run one independent subagent task in its own session and return its "
        "final answer. Use it for a self-contained investigation or change."
    )

    def __init__(
        self,
        *,
        workspace: Path,
        provider: Provider,
        tools: Sequence[Tool],
        budget: Budget,
        sessions_dir: Path,
        system_prompt: str,
        policy: Policy,
        approve: Callable[[ToolCallBlock], bool],
        context: Callable[[], Sequence[str]] | None = None,
        max_steps: int = 20,
    ) -> None:
        self.workspace = workspace.resolve()
        self.provider = provider
        self.tools = tuple(tools)
        self.budget = budget
        self.sessions_dir = sessions_dir
        self.system_prompt = system_prompt
        self.policy = policy
        self.approve = approve
        self.context = context or (lambda: ())
        self.max_steps = max_steps
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        }

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        prompt = _text_arg(call.args, "prompt").strip()
        if not prompt:
            raise ValueError("prompt must not be empty")
        child = SessionLog.create(self.sessions_dir / "subagents")
        child.append("system", {"text": self.system_prompt})
        child_tools = (*self.tools, HistoryTool(self.workspace, child))
        result = await run(
            child,
            prompt,
            provider=self.provider,
            tools=child_tools,
            budget=self.budget,
            policy=self.policy,
            approve=self.approve,
            context=tuple(self.context()),
            max_steps=self.max_steps,
            cancel=signal,
        )
        summary = result.answer.strip() or "(no answer)"
        header = (
            f"subagent {child.session_id} {result.termination} in {result.steps} steps"
        )
        content = f"{header}\n{summary}"
        if result.error:
            content += f"\nerror: {result.error}"
        return ToolResult(
            content=content,
            is_error=result.termination != "completed",
            metadata={
                "session_id": child.session_id,
                "termination": result.termination,
                "steps": result.steps,
            },
        )
