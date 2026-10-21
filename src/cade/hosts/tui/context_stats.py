"""TUI 状态栏上下文用量投影。"""

from __future__ import annotations

from typing import Any

from cade.harness.request import build
from cade.harness.session.projection import project

from .compat import ContextSummary


def _format_token(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def compute_context_summary(agent: Any) -> ContextSummary:
    """估算下一次请求的输入 token，并附带最近一次用量。"""
    service = getattr(agent, "_service", None)
    if service is None:
        return ContextSummary()
    report = None
    try:
        request = build(
            project(service.log.branch()),
            tools=tuple(service.tools),
            budget=service.budget,
            context=service.context(),
        )
        used = request.report.estimated_tokens
        report = request.report
    except (OSError, RuntimeError, TypeError, ValueError):
        used = 0
    window = service.budget.context_window
    usage = service.last_usage
    usage_stats = ""
    if usage:
        usage_stats = (
            f"in {_format_token(int(usage.get('input', 0)))} "
            f"out {_format_token(int(usage.get('output', 0)))}"
        )
    context_usage = (
        f"{_format_token(used)}/{_format_token(window)} ({used / window * 100:.1f}%)"
        if window
        else ""
    )
    return ContextSummary(
        snapshot=report,
        model_name=service.provider.model,
        usage_stats=usage_stats,
        context_usage=context_usage,
    )
