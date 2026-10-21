"""会话目标：持久化一个可查询、可完成的工作目标。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cade.core.loop import CancellationToken, ToolResult
from cade.core.types import ToolCallBlock
from cade.tools.workspace import _text_arg

_GOAL_PATH = Path(".cade") / "goal.json"


@dataclass(frozen=True)
class Goal:
    objective: str
    status: Literal["active", "done"]
    updated_at: str


class GoalStore:
    """工作区级目标存储；不进入会话日志 schema。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.path = self.workspace / _GOAL_PATH

    def load(self) -> Goal | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        objective = raw.get("objective")
        status = raw.get("status")
        if not isinstance(objective, str) or not objective.strip():
            return None
        if status not in {"active", "done"}:
            return None
        return Goal(
            objective=objective.strip(),
            status=status,
            updated_at=str(raw.get("updated_at", "")),
        )

    def set(self, objective: str) -> Goal:
        goal = Goal(
            objective=objective.strip(),
            status="active",
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._write(goal)
        return goal

    def complete(self) -> Goal | None:
        goal = self.load()
        if goal is None:
            return None
        done = Goal(
            objective=goal.objective,
            status="done",
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._write(done)
        return done

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def _write(self, goal: Goal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "objective": goal.objective,
                    "status": goal.status,
                    "updated_at": goal.updated_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class GoalTool:
    """读取或修改工作区目标。"""

    name = "goal"
    description = (
        "Read, set, complete, or clear the workspace goal. Use set with an "
        "objective for long-running work, and complete when it is achieved."
    )

    def __init__(self, workspace: Path) -> None:
        self.store = GoalStore(workspace)
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "complete", "clear"],
                },
                "objective": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        del signal
        action = _text_arg(call.args, "action").strip()
        if action == "get":
            goal = self.store.load()
            if goal is None:
                return ToolResult(content="No goal is set.")
            return ToolResult(
                content=f"{goal.status}: {goal.objective}",
                metadata={"status": goal.status},
            )
        if action == "set":
            goal = self.store.set(_text_arg(call.args, "objective"))
            return ToolResult(
                content=f"Goal set: {goal.objective}",
                metadata={"status": goal.status},
            )
        if action == "complete":
            goal = self.store.complete()
            if goal is None:
                return ToolResult(content="No goal is set.", is_error=True)
            return ToolResult(
                content=f"Goal completed: {goal.objective}",
                metadata={"status": goal.status},
            )
        if action == "clear":
            self.store.clear()
            return ToolResult(content="Goal cleared.")
        raise ValueError(f"unknown goal action: {action}")
