"""只读估算下一次模型请求的上下文用量。"""

from __future__ import annotations

import re
from html import unescape
from typing import TYPE_CHECKING

from cade.ai.types import ToolDefinition
from cade.coding.security.policy import filter_tools
from cade.harness.request import estimate
from cade.harness.session.projection import project
from cade.harness.session.types import (
    AssistantMessage,
    Message,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)

if TYPE_CHECKING:
    from .session import ApplicationSession


def _short(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def _preview(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", "", text).split())[:120]


def _sources(text: str) -> list[str]:
    """从实际请求文本提取来源，避免把磁盘文件误报为已加载。"""
    labels = {
        "project-instructions": "Project instructions",
        "working-note": "Working note",
        "memory": "Memory",
        "file": "Attached file",
        "skill": "Skill instructions",
    }
    sources: list[str] = []
    for tag, attributes in re.findall(
        r"<(project-instructions|working-note|memory|file|skill)\b([^>]*)>", text
    ):
        values = re.findall(
            r"""\b(?:source|path|name|root)=["'](.*?)["']""", attributes
        )
        if values:
            sources.append(" · ".join((labels[tag], *(unescape(v) for v in values))))
    if "<available-skills>" in text:
        sources.extend(
            f"Skill catalog · {unescape(name)}"
            for name in re.findall(r"<name>(.*?)</name>", text, re.DOTALL)
        )
    return list(dict.fromkeys(sources))


def _message_text(message: Message) -> str:
    if isinstance(message, AssistantMessage):
        return "\n".join(
            block.text for block in message.blocks if isinstance(block, TextBlock)
        )
    if isinstance(message, ToolResultMessage):
        return message.content
    return message.text


def _call_label(call: ToolCallBlock) -> str:
    if call.name == "skill":
        reference = call.args.get("reference")
        suffix = f" · reference: {reference}" if reference is not None else ""
        return f"skill · {call.args.get('name', '')}{suffix}"
    return f"{call.name} · {call.id}"


def format_context_usage(service: ApplicationSession) -> tuple[str, ...]:
    """展示实际预算与明确标注的本地估算，不修改会话。"""
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
    context = service.request_context()
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
    lines.extend(("", "System prompt:"))
    for message in system:
        lines.append(
            f"  ~{_short(estimate((message,)))} tokens · {_preview(message.text)}"
        )
    lines.extend(("", f"Request context · {len(context)} blocks:"))
    for index, part in enumerate(context, 1):
        tokens = estimate((), context=(part,))
        lines.append(f"  {index}. ~{_short(tokens)} tokens · {_preview(part)}")
        lines.extend(f"     {source}" for source in _sources(part))

    lines.extend(("", f"Tool definitions · {len(definitions)}:"))
    for definition in definitions:
        tokens = estimate((), tools=(definition,))
        lines.append(f"  {definition.name} · ~{_short(tokens)} tokens")

    calls = {
        block.id: _call_label(block)
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.blocks
        if isinstance(block, ToolCallBlock)
    }
    conversation = tuple(m for m in messages if not isinstance(m, SystemMessage))
    lines.extend(("", f"Conversation · {len(conversation)} messages:"))
    for index, message in enumerate(conversation, 1):
        label = message.role
        if isinstance(message, SummaryMessage):
            label += f" · {message.kind}"
        elif isinstance(message, ToolResultMessage):
            label += f" · {calls.get(message.call_id, message.call_id)}"
            if message.images:
                label += f" · {len(message.images)} images"
        elif isinstance(message, UserMessage) and message.source == "runtime":
            label += " · runtime guidance"
        text = _message_text(message)
        tokens = estimate((message,))
        lines.append(
            f"  {index}. {label} · ~{_short(tokens)} tokens · {_preview(text)}"
        )
        if isinstance(message, AssistantMessage):
            lines.extend(
                f"     {_call_label(block)}"
                for block in message.blocks
                if isinstance(block, ToolCallBlock)
            )
        lines.extend(f"     {source}" for source in _sources(text))
    return tuple(lines)
