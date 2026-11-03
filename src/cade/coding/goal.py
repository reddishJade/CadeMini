"""会话目标：持久化一个可查询、可完成的工作目标。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from cade.coding.tools.base import text_arg
from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult

_GOAL_PATH = Path(".cade") / "goal.json"


@dataclass(frozen=True)
class Goal:
    objective: str
    status: Literal["active", "done", "paused", "blocked"]
    updated_at: str
    blocked_reason: str | None = None


class GoalStore:
    """工作区级目标存储；不进入会话日志 schema。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.path = self.workspace / _GOAL_PATH
        self._armed_at: str | None = None
        self._lock = FileLock(str(self.path.with_suffix(".lock")))

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
        if status not in {"active", "done", "paused", "blocked"}:
            return None
        return Goal(
            objective=objective.strip(),
            status=status,
            updated_at=str(raw.get("updated_at", "")),
            blocked_reason=str(raw["blocked_reason"])
            if raw.get("blocked_reason")
            else None,
        )

    def set(self, objective: str) -> Goal:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not objective.strip():
                raise ValueError("Goal objective must not be empty.")
            goal = Goal(
                objective=objective.strip(),
                status="active",
                updated_at=datetime.now(UTC).isoformat(),
            )
            self._write(goal)
            self._armed_at = goal.updated_at
            return goal

    def runnable(self) -> Goal | None:
        """持久目标只有获得本进程授权后才能自动续跑。"""
        goal = self.load()
        if goal and goal.status == "active" and goal.updated_at == self._armed_at:
            return goal
        return None

    def disarm(self) -> None:
        self._armed_at = None

    def complete(self, *, updated_at: str | None = None) -> Goal | None:
        return self._transition("done", updated_at=updated_at)

    def pause(self) -> Goal | None:
        return self._transition("paused")

    def resume(self) -> Goal | None:
        return self._transition("active")

    def block(self, reason: str, *, updated_at: str | None = None) -> Goal | None:
        if not reason.strip():
            raise ValueError("A concrete blocker reason is required.")
        return self._transition("blocked", reason.strip(), updated_at=updated_at)

    def _transition(
        self,
        status: Literal["active", "done", "paused", "blocked"],
        blocked_reason: str | None = None,
        *,
        updated_at: str | None = None,
    ) -> Goal | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            goal = self.load()
            if goal is None:
                return None
            if updated_at is not None and updated_at != goal.updated_at:
                raise ValueError("The goal changed; read it again before updating.")
            if status != "done" and goal.status == "done":
                raise ValueError("The goal is already complete; set a new goal.")
            updated = Goal(
                objective=goal.objective,
                status=status,
                updated_at=datetime.now(UTC).isoformat(),
                blocked_reason=blocked_reason,
            )
            self._write(updated)
            self._armed_at = updated.updated_at if status == "active" else None
            return updated

    def clear(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.path.unlink(missing_ok=True)
            self.disarm()

    def _write(self, goal: Goal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "objective": goal.objective,
                    "status": goal.status,
                    "updated_at": goal.updated_at,
                    "blocked_reason": goal.blocked_reason,
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
        "Read or update the workspace goal. Set, clear, pause, and resume require "
        "a human input turn. Complete only after current evidence proves the "
        "whole objective; report a concrete blocker only after repeated attempts."
    )

    def __init__(
        self,
        workspace: Path,
        *,
        store: GoalStore | None = None,
        allow_control: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store or GoalStore(workspace)
        self.allow_control = allow_control
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "get",
                        "set",
                        "complete",
                        "clear",
                        "pause",
                        "resume",
                        "block",
                    ],
                },
                "objective": {"type": "string"},
                "reason": {"type": "string"},
                "updated_at": {
                    "type": "string",
                    "description": "Version returned by get; required for complete and block.",
                },
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
        action = text_arg(call.args, "action").strip()
        if (
            action in {"set", "clear", "pause", "resume"}
            and self.allow_control is not None
            and not self.allow_control()
        ):
            return ToolResult(
                content="This goal operation requires human input.", is_error=True
            )
        if action in {"complete", "block"}:
            current = self.store.load()
            if current is None:
                return ToolResult(content="No goal is set.", is_error=True)
            if call.args.get("updated_at") != current.updated_at:
                return ToolResult(
                    content="Read the current goal with get, then supply its updated_at version.",
                    is_error=True,
                )
        if action in {"pause", "resume", "block"}:
            if action == "pause":
                goal = self.store.pause()
            elif action == "resume":
                goal = self.store.resume()
            else:
                goal = self.store.block(
                    text_arg(call.args, "reason"),
                    updated_at=text_arg(call.args, "updated_at"),
                )
            if goal is None:
                return ToolResult(content="No goal is set.", is_error=True)
            return ToolResult(
                content=f"{goal.status}: {goal.objective}",
                metadata={"status": goal.status},
            )
        if action == "get":
            goal = self.store.load()
            if goal is None:
                return ToolResult(content="No goal is set.")
            return ToolResult(
                content=(
                    f"{goal.status}: {goal.objective}\nupdated_at: {goal.updated_at}"
                    + (
                        f"\nBlocked: {goal.blocked_reason}"
                        if goal.blocked_reason
                        else ""
                    )
                ),
                metadata={"status": goal.status, "updated_at": goal.updated_at},
            )
        if action == "set":
            goal = self.store.set(text_arg(call.args, "objective"))
            return ToolResult(
                content=f"Goal set: {goal.objective}",
                metadata={"status": goal.status},
            )
        if action == "complete":
            goal = self.store.complete(updated_at=text_arg(call.args, "updated_at"))
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
