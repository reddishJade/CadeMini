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
        "└ Context Usage",
        f"  {usage.model} · ~{_tokens(usage.used)}/{_tokens(usage.window)} tokens ({percentage:.1f}%)",
        "",
        "  Token usage by category · local estimates",
    ]
    for category in usage.categories:
        symbol = (
            "◉"
            if category.name in {"User messages", "Agent responses", "Tool calls"}
            else "⛁"
        )
        lines.append(
            f"  {symbol} {category.name}: {_tokens(category.tokens)} tokens "
            f"({category.tokens / usage.window * 100:.1f}%)"
        )
    free = max(0, usage.window - usage.used)
    lines.append(
        f"  □ Free space: {_tokens(free)} tokens ({free / usage.window * 100:.1f}%)"
    )
    if usage.catalog_count:
        lines.extend(("", f"  Skill catalog · {usage.catalog_count} available"))
    for kind, heading in (
        ("context", "Context files"),
        ("skill", "Skill files · /skill"),
        ("attachment", "Attached files"),
    ):
        files = [file for file in usage.files if file.kind == kind]
        if files:
            lines.extend(("", f"  {heading}"))
            lines.extend(
                f"   └ {compact_path(file.path)}: {_tokens(file.tokens)} tokens"
                for file in files
            )
    return "\n".join(lines)
