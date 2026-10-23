"""把上下文用量渲染为紧凑分类和文件来源。"""

from __future__ import annotations

from cade.app.context_report import ContextUsage

from .chrome import compact_path


def _tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def render_context_usage(usage: ContextUsage) -> str:
    """仅展示分类和来源，不展开提示词、工具定义或对话正文。"""
    percentage = usage.used / usage.window * 100
    lines = [
        f"Context · {usage.model}",
        f"  Estimated input: ~{_tokens(usage.used)} / {_tokens(usage.window)} tokens ({percentage:.1f}%)",
    ]
    if usage.last_request is not None:
        input_tokens, output_tokens = usage.last_request
        lines.append(
            f"  Last request (provider): {_tokens(input_tokens)} input · {_tokens(output_tokens)} output"
        )
    lines.extend(
        [
            "",
            "  Input sources (estimated)",
        ]
    )
    for category in usage.categories:
        if category.tokens == 0 and category.name not in {
            "User messages",
            "Agent responses",
            "Tool calls",
        }:
            continue
        label = category.name
        if label == "Skill catalog":
            label += f" ({usage.catalog_count} available)"
        lines.append(
            f"    {label}: ~{_tokens(category.tokens)} tokens "
            f"({category.tokens / usage.window * 100:.1f}%)"
        )
    remaining = max(0, usage.input_budget - usage.used)
    lines.extend(("", f"  Input remaining: ~{_tokens(remaining)} tokens"))
    for kind, heading in (
        ("context", "Loaded files"),
        ("skill", "Skill files"),
        ("attachment", "Attached files"),
    ):
        files = [file for file in usage.files if file.kind == kind]
        if files:
            lines.extend(("", f"  {heading}"))
            lines.extend(
                f"    {compact_path(file.path)}: ~{_tokens(file.tokens)} tokens"
                for file in files
            )
    if usage.mcp_servers:
        lines.extend(("", "  MCP schemas"))
        lines.extend(
            f"    {server.server}: {server.schemas} schemas · ~{_tokens(server.tokens)} tokens"
            for server in usage.mcp_servers
        )
    if usage.mcp_content:
        lines.extend(("", "  MCP content"))
        lines.extend(
            f"    {content.server} / {content.source}: ~{_tokens(content.tokens)} tokens"
            for content in usage.mcp_content
        )
    return "\n".join(lines)
