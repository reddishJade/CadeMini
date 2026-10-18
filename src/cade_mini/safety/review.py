"""自动审批复核：让模型对 ask 级工具调用给出 ALLOW 或 DENY。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cade_mini.ai.events import FinalMessage, ProviderFailure, TextDelta
from cade_mini.core.loop import Provider, ToolCallBlock

_SYSTEM = (
    "You are a conservative safety reviewer for a coding agent. Approve only "
    "a concrete, reversible, workspace-scoped action. Reply with exactly "
    "ALLOW or DENY."
)


class AutoReviewer:
    """用一次无工具模型调用复核工具请求；失败时拒绝。"""

    def __init__(
        self,
        provider: Provider,
        *,
        workspace: Path,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.provider = provider
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds

    async def __call__(self, call: ToolCallBlock) -> bool:
        arguments = json.dumps(
            call.args,
            ensure_ascii=False,
            sort_keys=True,
        )
        if len(arguments) > 4_000:
            arguments = arguments[:4_000] + "..."
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Workspace: {self.workspace}\n"
                    f"Tool: {call.name}\n"
                    f"Arguments: {arguments}\n\n"
                    "Reply ALLOW or DENY."
                ),
            },
        ]
        parts: list[str] = []
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async for event in self.provider.stream(messages, [], None):
                    if isinstance(event, TextDelta):
                        parts.append(event.chunk)
                    elif isinstance(event, FinalMessage):
                        if not parts:
                            parts.append(event.content)
                    elif isinstance(event, ProviderFailure):
                        return False
        except (TimeoutError, RuntimeError, ValueError):
            return False
        except Exception:  # noqa: BLE001
            return False
        answer = "".join(parts).strip().upper()
        return answer.startswith("ALLOW")
