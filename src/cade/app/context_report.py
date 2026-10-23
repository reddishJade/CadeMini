"""只读统计下一次请求的分类用量和实际文件来源。"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from cade.ai.models import get_model
from cade.ai.types import ToolDefinition
from cade.coding.security.policy import filter_tools
from cade.harness.request import estimate
from cade.harness.session.projection import project
from cade.harness.session.types import (
    AssistantMessage,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)

if TYPE_CHECKING:
    from .session import ApplicationSession

_FILE_BLOCK = re.compile(
    r"<(skill|file|project-instructions|working-note|memory)(?=[\s>])([^>]*)>"
    r"(.*?)</\1>",
    re.DOTALL,
)
_CATALOG = re.compile(
    r"<skill-activation>.*?</skill-activation>|<available-skills>.*?</available-skills>",
    re.DOTALL,
)


@dataclass(frozen=True)
class TokenCategory:
    name: str
    tokens: int


@dataclass(frozen=True)
class ContextFile:
    kind: Literal["context", "skill", "attachment"]
    path: Path
    tokens: int


@dataclass(frozen=True)
class ContextUsage:
    model: str
    used: int
    window: int
    categories: tuple[TokenCategory, ...]
    files: tuple[ContextFile, ...]
    catalog_count: int = 0


def _attributes(text: str) -> dict[str, str]:
    return {
        key: unescape(value)
        for key, _quote, value in re.findall(r"""([\w-]+)=(['"])(.*?)\2""", text)
    }


def _without_skills(text: str) -> str:
    return _FILE_BLOCK.sub(
        lambda match: (
            "" if match[1] == "skill" and "name" in _attributes(match[2]) else match[0]
        ),
        _CATALOG.sub("", text),
    )


def _file_sources(text: str, workspace: Path) -> list[ContextFile]:
    sources: list[ContextFile] = []
    for match in _FILE_BLOCK.finditer(text):
        tag, raw_attributes, _body = match.groups()
        attributes = _attributes(raw_attributes)
        tokens = estimate((SystemMessage(text=match[0]),)) - 4
        if tag == "skill" and "root" in attributes:
            sources.append(
                ContextFile("skill", Path(attributes["root"]) / "SKILL.md", tokens)
            )
        elif tag == "file" and "path" in attributes:
            sources.append(
                ContextFile("attachment", workspace / attributes["path"], tokens)
            )
        elif "source" in attributes:
            sources.append(
                ContextFile("context", workspace / attributes["source"], tokens)
            )
    return sources


def collect_context_usage(service: ApplicationSession) -> ContextUsage:
    """按实际请求归类；技能正文不再重复计入用户消息或工具结果。"""
    messages = project(service.log.branch())
    context = service.request_context()
    definitions = tuple(
        ToolDefinition(tool.name, tool.description, tool.parameters)
        for tool in filter_tools(service.tools, service.policy.mode)
    )
    system = tuple(m for m in messages if isinstance(m, SystemMessage))
    system_tokens = estimate(system, context=tuple(_without_skills(p) for p in context))
    counts: dict[str, int] = dict.fromkeys(
        (
            "User messages",
            "Agent responses",
            "Tool calls",
            "System prompt",
            "System tools",
            "Skills",
            "Subagents",
        ),
        0,
    )
    counts["System prompt"] = system_tokens
    counts["Skills"] = estimate(system, context=context) - system_tokens
    for definition in definitions:
        category = (
            "Subagents"
            if definition.name in {"subagent", "delegate"}
            else "System tools"
        )
        counts[category] += estimate((), tools=(definition,))

    calls = {
        block.id: block
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.blocks
        if isinstance(block, ToolCallBlock)
    }
    skill_paths = {skill.name: skill.file_path for skill in service.skills}
    sources = [
        source for part in context for source in _file_sources(part, service.workspace)
    ]
    for message in messages:
        if isinstance(message, UserMessage):
            remaining = estimate(
                (message.model_copy(update={"text": _without_skills(message.text)}),)
            )
            counts["User messages"] += remaining
            counts["Skills"] += estimate((message,)) - remaining
            sources.extend(_file_sources(message.text, service.workspace))
        elif isinstance(message, AssistantMessage):
            response = message.model_copy(
                update={
                    "blocks": tuple(
                        b for b in message.blocks if isinstance(b, TextBlock)
                    )
                }
            )
            counts["Agent responses"] += estimate((response,))
            for block in message.blocks:
                if isinstance(block, ToolCallBlock):
                    category = (
                        "Subagents"
                        if block.name in {"subagent", "delegate"}
                        else "Tool calls"
                    )
                    counts[category] += (
                        estimate((AssistantMessage(blocks=(block,)),)) - 4
                    )
        elif isinstance(message, ToolResultMessage):
            call = calls.get(message.call_id)
            category = "Tool calls"
            if call is not None and call.name in {"subagent", "delegate"}:
                category = "Subagents"
            elif call is not None and call.name == "skill" and not message.is_error:
                category = "Skills"
                name, reference = call.args.get("name"), call.args.get("reference")
                if (
                    isinstance(name, str)
                    and name in skill_paths
                    and isinstance(reference, str)
                ):
                    path = skill_paths[name].parent / "references" / reference
                    sources.append(ContextFile("skill", path, estimate((message,)) - 4))
            elif call is not None and call.name == "read" and not message.is_error:
                path = call.args.get("path")
                if isinstance(path, str):
                    sources.append(
                        ContextFile(
                            "context",
                            service.workspace / path,
                            estimate((message,)) - 4,
                        )
                    )
            counts[category] += estimate((message,))
            sources.extend(_file_sources(message.content, service.workspace))
        elif isinstance(message, SummaryMessage):
            counts["Context summaries"] = counts.get("Context summaries", 0) + estimate(
                (message,)
            )

    files: dict[tuple[str, Path], ContextFile] = {}
    for source in sources:
        key = (source.kind, source.path)
        previous = files.get(key)
        files[key] = replace(
            source, tokens=source.tokens + (previous.tokens if previous else 0)
        )
    model = get_model(service.provider_config.provider, service.provider.model)
    model_name = model.name if model else service.provider.model
    if effort := service.provider_config.reasoning_effort:
        model_name += f" ({effort.capitalize()})"
    return ContextUsage(
        model=model_name,
        used=estimate(messages, tools=definitions, context=context),
        window=service.budget.context_window,
        categories=tuple(
            TokenCategory(name, tokens) for name, tokens in counts.items()
        ),
        files=tuple(files.values()),
        catalog_count=sum(
            len(re.findall(r"<name>.*?</name>", part, re.DOTALL))
            for part in context
            if "<available-skills>" in part
        ),
    )
