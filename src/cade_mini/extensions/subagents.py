"""子代理与 delegate：在独立会话中执行一个或多个子任务。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cade_mini.core.log import SessionLog
from cade_mini.core.loop import (
    CancellationToken,
    Hooks,
    Policy,
    Provider,
    Tool,
    ToolResult,
    TurnLifecycle,
    run,
)
from cade_mini.core.permissions import Approver
from cade_mini.core.request import Budget
from cade_mini.core.types import JsonValue, ToolCallBlock
from cade_mini.safety.policy import Mode, WorkspacePolicy, filter_tools
from cade_mini.tools.history import HistoryTool
from cade_mini.tools.workspace import _text_arg


class SubagentRunner:
    """创建一个 child session 并运行一次任务；不递归。"""

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
        approve: Approver,
        context: Callable[[], Sequence[str]] | None = None,
        max_steps: int = 20,
        hooks: Hooks | None = None,
        lifecycle: TurnLifecycle | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.provider = provider
        self.tools = tuple(
            tool for tool in tools if tool.name not in {"subagent", "delegate"}
        )
        self.budget = budget
        self.sessions_dir = sessions_dir
        self.system_prompt = system_prompt
        self.policy = policy
        self.approve = approve
        self.context = context or (lambda: ())
        self.max_steps = max_steps
        self.hooks = hooks
        self.lifecycle = lifecycle

    async def run_task(
        self,
        prompt: str,
        *,
        mode: Mode | None = None,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        policy = self._policy_for(mode)
        child = SessionLog.create(self.sessions_dir / "subagents")
        child.append("system", {"text": self.system_prompt})
        child_tools: tuple[Tool, ...] = (
            *self.tools,
            HistoryTool(self.workspace, child),
        )
        if isinstance(policy, WorkspacePolicy):
            child_tools = filter_tools(child_tools, policy.mode)
        child_context = tuple(self.context())
        if isinstance(policy, WorkspacePolicy):
            child_context = (*child_context, policy.describe())
        result = await run(
            child,
            prompt,
            provider=self.provider,
            tools=child_tools,
            budget=self.budget,
            policy=policy,
            approve=self.approve,
            context=child_context,
            max_steps=self.max_steps,
            cancel=signal,
            hooks=self.hooks,
            lifecycle=self.lifecycle,
        )
        summary = result.answer.strip() or "(no answer)"
        header = f"{child.session_id} {result.termination} in {result.steps} steps"
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

    def _policy_for(self, mode: Mode | None) -> Policy:
        if isinstance(self.policy, WorkspacePolicy):
            selected = mode or self.policy.mode
            ranks = {"plan": 0, "ask": 1, "build": 2}
            if ranks[selected] > ranks[self.policy.mode]:
                raise ValueError("A subagent cannot expand its parent's execution mode")
            return WorkspacePolicy(
                self.workspace,
                read_only=self.policy.read_only,
                mode=selected,
                sandbox=self.policy.sandbox,
                temp_dir=self.policy.temp_dir,
                plan_dir=self.policy.plan_dir,
                rules=self.policy.rules,
                grants=self.policy.grants,
            )
        if mode is not None:
            raise ValueError("This policy does not support child mode overrides")
        return self.policy


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
        runner: SubagentRunner,
    ) -> None:
        self.runner = runner
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
        return await self.runner.run_task(
            _text_arg(call.args, "prompt"),
            signal=signal,
        )


class DelegateTool:
    """一次启动一个或多个子会话；每个任务独立执行并汇总结果。"""

    name = "delegate"
    description = (
        "Delegate one or more self-contained tasks to child sessions. "
        "Tasks run sequentially and return their session IDs and answers."
    )

    def __init__(self, *, runner: SubagentRunner) -> None:
        self.runner = runner
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "description": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["plan", "build", "ask"],
                },
            },
            "additionalProperties": False,
        }

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        tasks = _tasks(call.args)
        mode = _mode(call.args)
        results: list[ToolResult] = []
        for prompt in tasks:
            results.append(
                await self.runner.run_task(
                    prompt,
                    mode=mode,
                    signal=signal,
                )
            )
        failed = any(result.is_error for result in results)
        content = "\n\n".join(
            f"[{index + 1}/{len(results)}]\n{result.content}"
            for index, result in enumerate(results)
        )
        return ToolResult(
            content=content,
            is_error=failed,
            metadata={
                "tasks": len(results),
                "sessions": [
                    result.metadata.get("session_id")
                    for result in results
                    if result.metadata
                ],
            },
        )


def _tasks(args: dict[str, JsonValue]) -> list[str]:
    raw = args.get("tasks")
    if isinstance(raw, list):
        tasks = [str(item).strip() for item in raw if str(item).strip()]
        if tasks:
            return tasks
    prompt = args.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return [prompt]
    raise ValueError("prompt or tasks is required")


def _mode(args: dict[str, JsonValue]) -> Mode | None:
    mode = args.get("mode")
    if mode is None:
        return None
    if mode == "plan":
        return "plan"
    if mode == "build":
        return "build"
    if mode == "ask":
        return "ask"
    raise ValueError(f"invalid mode: {mode!r}")
