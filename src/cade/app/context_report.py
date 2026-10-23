"""只读统计下一次请求的分类用量和实际文件来源。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import partial
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from cade.ai.models import get_model
from cade.ai.providers.base import InputTokenCounter, TextTokenizer
from cade.ai.tokenization import TokenCounter
from cade.ai.types import ToolDefinition
from cade.coding.mcp.tools import McpTool
from cade.coding.security.policy import filter_tools
from cade.harness.request import build
from cade.harness.request import estimate as estimate_request
from cade.harness.session.projection import project
from cade.harness.session.types import (
    AssistantMessage,
    LogEntry,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)
from cade.harness.tools import Tool

if TYPE_CHECKING:
    from .session import ApplicationSession

_FILE_BLOCK = re.compile(
    r"<(skill|file|project-instructions|working-note|memory)(?=[\s>])([^>]*)>"
    r"(.*?)</\1>",
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
class McpUsage:
    server: str
    schemas: int
    tokens: int


@dataclass(frozen=True)
class McpContent:
    server: str
    source: str
    tokens: int


@dataclass(frozen=True)
class ContextUsage:
    model: str
    used: int
    window: int
    input_budget: int
    categories: tuple[TokenCategory, ...]
    files: tuple[ContextFile, ...]
    catalog_count: int = 0
    mcp_servers: tuple[McpUsage, ...] = ()
    mcp_content: tuple[McpContent, ...] = ()
    last_request: tuple[int, int] | None = None
    measured_input: int | None = None
    counter_label: str = "character heuristic"
    count_error: str | None = None


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
        text,
    )


def _file_sources(
    text: str, workspace: Path, counter: TokenCounter | None
) -> list[ContextFile]:
    sources: list[ContextFile] = []
    for match in _FILE_BLOCK.finditer(text):
        tag, raw_attributes, _body = match.groups()
        attributes = _attributes(raw_attributes)
        tokens = (
            estimate_request(
                (SystemMessage(text=match[0]),),
                count_tokens=counter.count if counter else None,
            )
            - 4
        )
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
    """离线收集上下文来源，不触发 provider 或模型文件下载。"""
    entries = service.log.branch()
    return _collect_context_usage(
        service,
        entries,
        tuple(service.request_context(entries)),
        filter_tools(service.tools, service.policy.mode),
        None,
    )


async def inspect_context_usage(service: ApplicationSession) -> ContextUsage:
    """按同一份快照做模型分词和远端预计数，不生成回复。"""
    entries = service.log.branch()
    context = tuple(service.request_context(entries))
    tools = filter_tools(service.tools, service.policy.mode)
    counter = None
    error = None
    if isinstance(service.provider, TextTokenizer):
        try:
            counter = await service.provider.load_token_counter()
        except (OSError, ValueError, httpx.HTTPError) as exc:
            error = f"Tokenizer unavailable: {exc}"
    usage = _collect_context_usage(service, entries, context, tools, counter)
    if isinstance(service.provider, InputTokenCounter):
        definitions = tuple(
            ToolDefinition(t.name, t.description, t.parameters) for t in tools
        )
        request = build(
            project(entries), tools=definitions, budget=service.budget, context=context
        )
        try:
            measured = await service.provider.count_input_tokens(
                list(request.messages), list(request.tools), request.options
            )
            usage = replace(usage, measured_input=measured)
        except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
            error = f"Provider count unavailable: {exc}"
    return replace(usage, count_error=error)


def _collect_context_usage(
    service: ApplicationSession,
    entries: Sequence[LogEntry],
    context: tuple[str, ...],
    tools: Sequence[Tool],
    counter: TokenCounter | None,
) -> ContextUsage:
    """按实际请求归类；技能正文不再重复计入用户消息或工具结果。"""
    messages = project(entries)
    estimate = partial(
        estimate_request, count_tokens=counter.count if counter else None
    )
    definitions = tuple(
        ToolDefinition(tool.name, tool.description, tool.parameters) for tool in tools
    )
    system = tuple(m for m in messages if isinstance(m, SystemMessage))
    counts: dict[str, int] = dict.fromkeys(
        (
            "User messages",
            "Agent responses",
            "Tool calls",
        ),
        0,
    )
    counts["System prompt"] = estimate(system)
    previous = counts["System prompt"]
    for index, part in enumerate(context):
        category = _context_category(part)
        current = estimate(system, context=context[: index + 1])
        counts[category] = counts.get(category, 0) + current - previous
        previous = current
    mcp_servers: dict[str, McpUsage] = {}
    for tool, definition in zip(tools, definitions, strict=True):
        category = "MCP tools" if isinstance(tool, McpTool) else "Built-in tools"
        tokens = estimate((), tools=(definition,))
        counts[category] = counts.get(category, 0) + tokens
        if isinstance(tool, McpTool):
            server = mcp_servers.get(tool.server, McpUsage(tool.server, 0, 0))
            mcp_servers[tool.server] = replace(
                server, schemas=server.schemas + 1, tokens=server.tokens + tokens
            )

    calls = {
        block.id: block
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.blocks
        if isinstance(block, ToolCallBlock)
    }
    skill_paths = {skill.name: skill.file_path for skill in service.skills}
    metadata = {}
    for entry in entries:
        artifact = entry.data.get("artifact")
        if entry.type == "tool_result" and isinstance(artifact, dict):
            tool_metadata = artifact.get("tool_metadata")
            call_id = entry.data.get("call_id")
            if isinstance(tool_metadata, dict) and isinstance(call_id, str):
                metadata[call_id] = tool_metadata
    mcp_tools = {tool.name: tool for tool in tools if isinstance(tool, McpTool)}
    mcp_content: dict[tuple[str, str], McpContent] = {}
    sources = [
        source
        for part in context
        for source in _file_sources(part, service.workspace, counter)
    ]
    for message in messages:
        if isinstance(message, UserMessage):
            remaining = estimate(
                (message.model_copy(update={"text": _without_skills(message.text)}),)
            )
            counts["User messages"] += remaining
            counts["Loaded skills"] = (
                counts.get("Loaded skills", 0) + estimate((message,)) - remaining
            )
            sources.extend(_file_sources(message.text, service.workspace, counter))
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
                    counts["Tool calls"] += (
                        estimate((AssistantMessage(blocks=(block,)),)) - 4
                    )
        elif isinstance(message, ToolResultMessage):
            call = calls.get(message.call_id)
            category = "Tool results"
            remote = mcp_tools.get(call.name) if call is not None else None
            tool_metadata = metadata.get(message.call_id, {})
            server = tool_metadata.get("mcp_server") or (
                remote.server if remote else None
            )
            if isinstance(server, str) and call is not None:
                category = "MCP results"
                source = (
                    call.args.get("uri")
                    if call.name.endswith("__resources_read")
                    else None
                )
                if call.name.endswith("__prompts_get"):
                    source = f"prompt:{call.args.get('name', '')}"
                source = (
                    source
                    or tool_metadata.get("mcp_tool")
                    or (remote.remote_name if remote else call.name)
                )
                if not message.is_error:
                    key = (server, str(source))
                    previous_content = mcp_content.get(key)
                    tokens = estimate((message,)) + (
                        previous_content.tokens if previous_content else 0
                    )
                    mcp_content[key] = McpContent(server, str(source), tokens)
            elif call is not None and call.name == "skill" and not message.is_error:
                category = "Loaded skills"
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
            counts[category] = counts.get(category, 0) + estimate((message,))
            sources.extend(_file_sources(message.content, service.workspace, counter))
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
        input_budget=service.budget.input_budget,
        categories=tuple(
            TokenCategory(name, tokens) for name, tokens in counts.items()
        ),
        files=tuple(files.values()),
        catalog_count=sum(
            len(re.findall(r"<name>.*?</name>", part, re.DOTALL))
            for part in context
            if "<available-skills>" in part
        ),
        mcp_servers=tuple(mcp_servers.values()),
        mcp_content=tuple(mcp_content.values()),
        last_request=_last_request_usage(entries, service.provider.model),
        counter_label=counter.label if counter else "character heuristic",
    )


def _context_category(text: str) -> str:
    for marker, name in (
        ("<project-instructions ", "Project instructions"),
        ("<working-note ", "Working note"),
        ("<memory ", "Memory"),
        ("<goal>", "Active goal"),
        ("<skill-activation>", "Skill catalog"),
        ("<available-skills>", "Skill catalog"),
        ("<skill name=", "Loaded skills"),
    ):
        if text.lstrip().startswith(marker):
            return name
    return "System context"


def _last_request_usage(
    entries: Sequence[LogEntry], model: str
) -> tuple[int, int] | None:
    """取最近一次请求的 provider 用量，不拿整轮任务的累计值代替。"""
    for entry in reversed(entries):
        if entry.type not in {"assistant", "attempt", "compaction"}:
            continue
        usage = entry.data.get("usage")
        if entry.data.get("model") == model and isinstance(usage, dict):
            input_tokens, output_tokens = usage.get("input"), usage.get("output")
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                return input_tokens, output_tokens
    return None
