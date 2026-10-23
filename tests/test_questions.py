"""验证提问、答案和取消行为。"""

from __future__ import annotations

from pathlib import Path

from cade.coding.security.policy import WorkspacePolicy, filter_tools
from cade.coding.tools.question import Question, QuestionTool
from cade.harness.cancellation import CancellationToken
from cade.harness.executor import execute_tool
from cade.harness.session.types import ToolCallBlock


async def test_question_answers_and_dismissal(tmp_path: Path) -> None:
    async def answer(questions: tuple[Question, ...]) -> list[list[str]]:
        return [["Build"], []]

    tool = QuestionTool(answer)
    call = ToolCallBlock(
        id="q",
        name="question",
        args={
            "questions": [
                {
                    "question": "What should happen?",
                    "header": "Action",
                    "options": [{"label": "Build", "description": "Build it"}],
                },
                {"question": "Anything else?", "header": "Optional", "options": []},
            ]
        },
    )
    for mode in ("plan", "build", "ask"):
        assert filter_tools((tool,), mode)
        result = await execute_tool(
            call, {"question": tool}, policy=WorkspacePolicy(tmp_path, mode=mode)
        )
        assert (
            result.result.content
            == 'User has answered your questions: "What should happen?"="Build", "Anything else?"="Unanswered". You can now continue with the user\'s answers in mind.'
        )
        assert result.result.metadata == {"answers": [["Build"], []]}

    async def dismiss(questions: tuple[Question, ...]) -> None:
        return None

    token = CancellationToken()
    result = await execute_tool(call, {"question": QuestionTool(dismiss)}, cancel=token)
    assert token.cancelled
    assert result.result.content == "The user dismissed this question"
