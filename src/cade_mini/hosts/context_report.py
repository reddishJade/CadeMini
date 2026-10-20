"""Read-only, local estimate of the next model request's context usage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cade_mini.ai.types import ToolDefinition
from cade_mini.core.projection import project
from cade_mini.core.request import estimate
from cade_mini.core.types import (
    AssistantMessage,
    SummaryMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from cade_mini.safety.policy import filter_tools

if TYPE_CHECKING:
    from .interaction import InteractionService


def _short(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def format_context_usage(service: InteractionService) -> tuple[str, ...]:
    """Expose actual budget and clearly labeled local estimates, without mutation."""
    messages = project(service.log.branch())
    available_tools = filter_tools(service.tools, service.policy.mode)
    definitions = tuple(
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
        )
        for tool in available_tools
    )
    context = tuple(service.context())
    system = tuple(m for m in messages if isinstance(m, SystemMessage))
    summaries = tuple(m for m in messages if isinstance(m, SummaryMessage))
    working = tuple(
        m for m in messages if isinstance(m, UserMessage | AssistantMessage)
    )
    evidence = tuple(m for m in messages if isinstance(m, ToolResultMessage))

    fixed = estimate(system, tools=definitions, context=context)
    durable = estimate(summaries)
    working_tokens = estimate(working)
    evidence_tokens = estimate(evidence)
    total = estimate(messages, tools=definitions, context=context)
    budget = service.budget
    remaining = max(0, budget.input_budget - total)
    percentage = 100 * total / budget.context_window
    lines = [
        (
            f"Context Usage · {service.provider.model} · "
            f"~{_short(total)}/{_short(budget.context_window)} ({percentage:.1f}%)"
        ),
        "",
        "Local estimates:",
        f"  {'Fixed prefix':<19}{_short(fixed):>8} tokens",
        f"  {'Durable state':<19}{_short(durable):>8} tokens",
        f"  {'Working messages':<19}{_short(working_tokens):>8} tokens",
        f"  {'Tool evidence':<19}{_short(evidence_tokens):>8} tokens",
        f"Input budget: {_short(budget.input_budget)}",
        f"Remaining input: {_short(remaining)}",
        (
            f"Output reserve: {_short(budget.output_reserve)}"
            f" · Headroom: {_short(budget.headroom_tokens)}"
        ),
    ]
    if service.last_usage:
        last = service.last_usage
        lines.append(
            f"Last run · input {_short(last.get('input', 0))}"
            f" · output {_short(last.get('output', 0))}"
        )
    instruction_files = tuple(
        service.workspace / name
        for name in ("AGENTS.md", "NOTE.md", "MEMORY.md")
        if (service.workspace / name).is_file()
    )
    if instruction_files:
        lines.extend(("", "Workspace files · detected"))
        lines.extend(f"  └ {path}" for path in instruction_files)
    if service.skills:
        lines.extend(("", f"Skills · {len(service.skills)} available via $name"))
    return tuple(lines)
