"""Cade-style syntax highlighting, prompt and inline completion menu."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import cast

from prompt_toolkit.application import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import UIContent
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.utils import get_cwidth

from .chrome import fit_text

_FILE_REF = re.compile(r"(?<!\S)@([^\s]+)")


class TuiInputLexer(Lexer):
    """Mark attached @file references without changing their text."""

    def lex_document(self, document: object) -> Callable[[int], StyleAndTextTuples]:
        lines: list[str] = (
            getattr(document, "lines", None)
            or str(getattr(document, "text", "")).splitlines()
        )
        if not lines:
            lines = [""]

        def get_line(line_number: int) -> StyleAndTextTuples:
            if line_number < 0 or line_number >= len(lines):
                return []
            line = lines[line_number]
            fragments: list[tuple[str, str]] = []
            cursor = 0
            for match in _FILE_REF.finditer(line):
                if match.start() > cursor:
                    fragments.append(("", line[cursor : match.start()]))
                fragments.append(("fg:ansicyan bold", match.group(0)))
                cursor = match.end()
            if cursor < len(line):
                fragments.append(("", line[cursor:]))
            return cast(StyleAndTextTuples, fragments or [("", line)])

        return get_line


class TuiCompletionControl(CompletionsMenuControl):
    """Constrain completion labels and descriptions to terminal width."""

    def create_content(self, width: int, height: int) -> UIContent:
        state = get_app().current_buffer.complete_state
        if state is None or not state.completions:
            return UIContent()
        index = state.complete_index or 0
        name_width = min(
            max(get_cwidth(item.display_text) for item in state.completions),
            max(1, width // 2 - 2),
        )

        def get_line(line: int) -> StyleAndTextTuples:
            item = state.completions[line]
            suffix = ".current" if line == index else ""
            name = fit_text(item.display_text, name_width)
            padding = " " * max(0, name_width - get_cwidth(name) + 2)
            description = fit_text(item.display_meta_text, width - name_width - 4)
            return [
                (f"class:completion-menu.completion{suffix}",
                    f"{'›' if line == index else ' '} {name}{padding}"),
                (f"class:completion-menu.meta.completion{suffix}", description),
            ]

        return UIContent(
            get_line=get_line,
            cursor_position=Point(x=0, y=index),
            line_count=len(state.completions),
        )


def input_prompt(shell_command: bool) -> StyleAndTextTuples:
    return [("class:prompt-marker", "$ " if shell_command else "> ")]
