"""验证不完整轮次的终端停止提示。"""

from __future__ import annotations

import pytest

from cade.hosts.tui.compat import (
    AgentHarnessResult,
    FinalStructuredEvent,
    final_stop_reason,
)
from cade.hosts.tui.state import _TuiState


@pytest.mark.parametrize(
    ("termination", "expected"),
    [
        ("context_error", "context budget exceeded"),
        ("max_tokens", "output token limit reached"),
    ],
)
def test_tui_renders_incomplete_results(termination: str, expected: str) -> None:
    state = _TuiState()
    state.handle_event(
        FinalStructuredEvent(AgentHarnessResult(stop_reason=termination))
    )
    assert expected in state.render()


@pytest.mark.parametrize(
    ("termination", "error", "expected"),
    [
        (
            "context_error",
            "request needs 40000 tokens",
            "[stopped] request needs 40000 tokens",
        ),
        ("max_tokens", None, "[stopped] output token limit reached"),
    ],
)
def test_tui_shows_incomplete_termination(
    termination: str, error: str | None, expected: str
) -> None:
    assert (
        final_stop_reason(AgentHarnessResult(stop_reason=termination, error=error))
        == expected
    )
