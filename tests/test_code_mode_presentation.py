"""Code Mode 的操作说明在实时和恢复后的工具卡片中保持一致。"""

import pytest

from cade.hosts.tui.state import _TuiState


@pytest.mark.parametrize("restored", [False, True])
def test_run_code_card_uses_description_instead_of_program(restored: bool) -> None:
    state = _TuiState()
    state.set_verbosity("high")
    args = {"code": "print('raw-program-marker')", "description": "Count file lines"}
    if restored:
        state._restore_event(
            {
                "type": "tool_use",
                "data": {"id": "script", "name": "run_code", "input": args},
            }
        )
    else:
        state._record_tool_use("script", "run_code", args)
    rendered = state.render()
    assert "Count file lines" in rendered
    assert "raw-program-marker" not in rendered


def test_old_run_code_card_without_description_does_not_dump_program() -> None:
    state = _TuiState()
    state.set_verbosity("high")
    state._record_tool_use("old", "run_code", {"code": "print('raw-program-marker')"})
    assert "raw-program-marker" not in state.render()
