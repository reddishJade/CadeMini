"""Cade Mini 请求构造的回归测试。"""

from __future__ import annotations

import copy

import pytest

from cade_mini.ai.types import ToolDefinition
from cade_mini.core.request import Budget, RequestTooLarge, build, estimate
from cade_mini.core.types import (
    AssistantMessage,
    Message,
    SummaryMessage,
    SystemMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
)


def _user(text: str) -> UserMessage:
    return UserMessage(text=text)


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(blocks=(TextBlock(text=text),))


def _tool_result(call_id: str, content: str) -> ToolResultMessage:
    return ToolResultMessage(call_id=call_id, content=content)


def test_build_under_budget_keeps_all_messages() -> None:
    conversation = [
        SystemMessage(text="sys"),
        _user("hi"),
        _assistant("hello"),
    ]

    request = build(conversation, budget=Budget(100_000))

    assert request.report.over_budget is False
    assert [message["role"] for message in request.messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert request.messages[0]["content"] == "sys"


def test_build_merges_context_into_one_system_message() -> None:
    conversation = [SystemMessage(text="base"), _user("hi")]

    request = build(
        conversation,
        budget=Budget(100_000),
        context=("ctx one", "ctx two"),
    )

    assert request.messages[0] == {
        "role": "system",
        "content": "base\n\nctx one\n\nctx two",
    }
    assert sum(1 for message in request.messages if message["role"] == "system") == 1
    assert request.report.context_tokens > 0


def test_build_prunes_old_tool_evidence_but_keeps_recent() -> None:
    conversation: list[Message] = [_user("run")]
    for index in range(1, 6):
        conversation.append(
            AssistantMessage(blocks=(ToolCallBlock(id=f"c{index}", name="bash"),))
        )
        conversation.append(
            _tool_result(
                f"c{index}",
                "x" * 20_000 if index == 1 else "small",
            )
        )

    request = build(
        conversation,
        budget=Budget(context_window=5_000, output_reserve=0, headroom_tokens=0),
    )

    assert request.report.over_budget is False
    assert request.report.dropped_tool_calls == ("c1",)
    assert request.report.dropped_turns == 0
    tool_messages = [
        message for message in request.messages if message["role"] == "tool"
    ]
    assert tool_messages[0]["content"].startswith("[tool output omitted")
    assert tool_messages[0]["content"].count("use the history tool") == 1
    assert tool_messages[1]["content"] == "small"


def test_build_prunes_a_single_huge_tool_result() -> None:
    conversation = [
        _user("run"),
        AssistantMessage(blocks=(ToolCallBlock(id="c1", name="read"),)),
        _tool_result("c1", "x" * 20_000),
    ]

    request = build(
        conversation,
        budget=Budget(context_window=5_000, output_reserve=0, headroom_tokens=0),
    )

    assert request.report.over_budget is False
    assert request.report.dropped_tool_calls == ("c1",)
    tool_message = next(
        message for message in request.messages if message["role"] == "tool"
    )
    assert tool_message["content"].startswith("[tool output omitted")


def test_build_drops_oldest_turn_when_no_tool_evidence() -> None:
    conversation = [
        _user("old " * 5_000),
        _assistant("old answer " * 5_000),
        _user("new"),
        _assistant("done"),
    ]

    request = build(
        conversation,
        budget=Budget(context_window=5_000, output_reserve=0, headroom_tokens=0),
    )

    assert request.report.over_budget is False
    assert request.report.dropped_turns == 1
    assert request.report.dropped_tool_calls == ()
    assert request.messages[0]["role"] == "user"
    assert "new" in str(request.messages[0]["content"])


def test_build_reports_over_budget_when_system_alone_is_too_large() -> None:
    request = build(
        [SystemMessage(text="x" * 20_000), _user("hi")],
        budget=Budget(context_window=1_000, output_reserve=0, headroom_tokens=0),
    )

    assert request.report.over_budget is True
    with pytest.raises(RequestTooLarge):
        request.require_sendable()


def test_build_is_pure() -> None:
    conversation = [
        SystemMessage(text="sys"),
        _user("hi"),
        AssistantMessage(
            blocks=(
                TextBlock(text="hello"),
                ToolCallBlock(id="c1", name="bash", args={"command": "pwd"}),
            )
        ),
        _tool_result("c1", "ok"),
    ]
    before = copy.deepcopy(conversation)

    build(
        conversation,
        budget=Budget(context_window=2_000, output_reserve=0, headroom_tokens=0),
    )

    assert conversation == before


def test_wire_summary_assistant_and_tool_result() -> None:
    conversation = [
        SummaryMessage(text="checkpoint", source_id="e1", kind="window"),
        AssistantMessage(
            blocks=(
                TextBlock(text="hello"),
                ToolCallBlock(id="c1", name="bash", args={"command": "pwd"}),
            ),
            provider_state={"items": [{"type": "message"}]},
        ),
        _tool_result("c1", "ok"),
    ]

    request = build(conversation, budget=Budget(100_000))

    assert request.messages[0]["role"] == "user"
    assert "<window-handoff" in str(request.messages[0]["content"])
    assistant = request.messages[1]
    assert assistant["content"] == "hello"
    assert assistant["_cade_mini_replay_payload"] == {"items": [{"type": "message"}]}
    call = assistant["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["function"]["name"] == "bash"
    assert '"command":"pwd"' in call["function"]["arguments"]
    assert request.messages[2]["role"] == "tool"


def test_build_digests_change_with_input() -> None:
    first = build([_user("one")], budget=Budget(100_000))
    second = build([_user("two")], budget=Budget(100_000))

    assert first.conversation_digest != second.conversation_digest
    assert first.digest != second.digest
    assert first.tools_digest == second.tools_digest


def test_estimate_counts_context() -> None:
    base = estimate([_user("hi")])
    with_context = estimate([_user("hi")], context=("x" * 1_000,))

    assert with_context > base


def test_tools_contribute_to_budget() -> None:
    tool = ToolDefinition(
        name="huge",
        description="d" * 20_000,
        parameters={"type": "object"},
    )

    request = build(
        [_user("hi")],
        tools=(tool,),
        budget=Budget(context_window=1_000, output_reserve=0, headroom_tokens=0),
    )

    assert request.report.over_budget is True


def test_budget_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        Budget(context_window=0)
    with pytest.raises(ValueError):
        Budget(context_window=100, output_reserve=100, headroom_tokens=1)
