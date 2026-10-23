"""规范化提问参数、答案格式和取消行为。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import JsonValue, ToolCallBlock
from cade.harness.tools import ToolDismissed, ToolResult


@dataclass(frozen=True)
class Question:
    text: str
    options: tuple[tuple[str, str], ...]
    header: str
    multiple: bool = False


type AnswerHandler = Callable[[tuple[Question, ...]], Awaitable[list[list[str]] | None]]


class QuestionTool:
    name = "question"
    description = """Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- A "Type your own answer" option is added automatically; don't include a separate option for free form answers
- Set `multiple: true` to allow selecting more than one option
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label"""
    manages_timeout = True

    def __init__(self, answer: AnswerHandler) -> None:
        self.answer = answer
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Questions to ask",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "Complete question",
                            },
                            "header": {
                                "type": "string",
                                "description": "Very short label (max 30 chars)",
                            },
                            "options": {
                                "type": "array",
                                "description": "Available choices",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": "Display text (1-5 words, concise)",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "Explanation of choice",
                                        },
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "multiple": {
                                "type": "boolean",
                                "description": "Allow selecting multiple choices",
                            },
                        },
                        "required": ["question", "header", "options"],
                    },
                }
            },
            "required": ["questions"],
        }

    async def execute(
        self, call: ToolCallBlock, signal: CancellationToken | None = None
    ) -> ToolResult:
        questions = parse_questions(call.args.get("questions"))
        if signal is not None and signal.cancelled:
            raise ToolDismissed("The user dismissed this question")
        answers = await self.answer(questions)
        if answers is None or (signal is not None and signal.cancelled):
            raise ToolDismissed("The user dismissed this question")
        formatted = ", ".join(
            f'"{question.text}"="{", ".join(answers[index]) if index < len(answers) and answers[index] else "Unanswered"}"'
            for index, question in enumerate(questions)
        )
        return ToolResult(
            f"User has answered your questions: {formatted}. You can now continue with the user's answers in mind.",
            metadata={"answers": answers},
        )


def parse_questions(raw: JsonValue | None) -> tuple[Question, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty array")
    result: list[Question] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("each question must be an object")
        text, header, options = (
            item.get("question"),
            item.get("header"),
            item.get("options"),
        )
        if (
            not isinstance(text, str)
            or not isinstance(header, str)
            or not isinstance(options, list)
        ):
            raise TypeError("question, header and options are required")
        parsed: list[tuple[str, str]] = []
        for option in options:
            if (
                not isinstance(option, dict)
                or not isinstance(option.get("label"), str)
                or not isinstance(option.get("description"), str)
            ):
                raise TypeError("each option requires label and description")
            parsed.append((str(option["label"]), str(option["description"])))
        multiple = item.get("multiple", False)
        if not isinstance(multiple, bool):
            raise TypeError("multiple must be boolean")
        result.append(Question(text, tuple(parsed), header, multiple))
    return tuple(result)
