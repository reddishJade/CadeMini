"""Host-neutral question tool: normalize prompts and delegate answers to the host."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cade_mini.core.loop import CancellationToken, ToolResult
from cade_mini.core.types import JsonValue, ToolCallBlock


@dataclass(frozen=True)
class Question:
    text: str
    options: tuple[tuple[str, str], ...] = ()
    multiple: bool = False
    header: str = ""


type AnswerHandler = Callable[[tuple[Question, ...]], Awaitable[list[list[str]]]]


class QuestionTool:
    """Request choices or free-text answers from a connected interactive host."""

    name = "question"
    description = (
        "Ask the user a question before proceeding. Provide options for a "
        "single- or multiple-choice decision, or omit options for free text. "
        "Do this only when a user decision is required."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "header": {"type": "string", "maxLength": 30},
                        "question": {"type": "string"},
                        "multiple": {"type": "boolean"},
                        "options": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label"],
                            },
                        },
                    },
                    "required": ["question"],
                },
            }
        },
        "required": ["questions"],
    }

    def __init__(self, answer: AnswerHandler) -> None:
        self.answer = answer

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        questions = parse_questions(call.args.get("questions"))
        if signal is not None and signal.cancelled:
            return ToolResult(content="Question cancelled.", is_error=True)
        answers = await self.answer(questions)
        if signal is not None and signal.cancelled:
            return ToolResult(content="Question cancelled.", is_error=True)
        if len(answers) != len(questions):
            return ToolResult(content="Question was not answered.", is_error=True)
        pieces = []
        for question, values in zip(questions, answers, strict=True):
            if not values:
                pieces.append(f'{question.text}: unanswered')
            else:
                pieces.append(f'{question.text}: {", ".join(values)}')
        return ToolResult(content="User answers:\n" + "\n".join(pieces))


def parse_questions(raw: JsonValue | None) -> tuple[Question, ...]:
    """Validate untrusted model input before opening terminal controls."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions must be a non-empty array")
    parsed: list[Question] = []
    for value in raw:
        if not isinstance(value, dict):
            raise ValueError("each question must be an object")
        question = value.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question text must be non-empty")
        header = value.get("header")
        if header is None:
            header = ""
        if not isinstance(header, str) or len(header) > 30:
            raise ValueError("question header must be 0-30 characters")
        multiple = value.get("multiple")
        if multiple is None:
            multiple = False
        if not isinstance(multiple, bool):
            raise ValueError("question multiple must be boolean")
        raw_options = value.get("options")
        if raw_options is None:
            raw_options = []
        if not isinstance(raw_options, list):
            raise ValueError("question options must be an array")
        options: list[tuple[str, str]] = []
        for item in raw_options:
            if not isinstance(item, dict):
                raise ValueError("question option must be an object")
            label = item.get("label")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("question option label is required")
            description = item.get("description")
            if description is None:
                description = ""
            if not isinstance(description, str):
                raise ValueError("question description must be text")
            options.append((label.strip(), description.strip()))
        if len({name for name, _ in options}) != len(options):
            raise ValueError("question options must have unique labels")
        parsed.append(
            Question(
                text=question.strip(),
                options=tuple(options),
                multiple=multiple,
                header=header.strip(),
            )
        )
    return tuple(parsed)
