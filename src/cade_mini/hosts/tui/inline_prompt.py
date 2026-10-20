"""Inline, cancellable terminal prompts shared by first-run onboarding.

Unlike prompt_toolkit.shortcuts.*_dialog these keep the terminal's own background,
scrollback and colors. They never enter the full-screen alternate buffer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import (
    Dimension,
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import RadioList, TextArea

from .chrome import TUI_STYLES
from .platform import preserve_console_input_mode

_STYLE = Style.from_dict(TUI_STYLES)


def _heading(step: str) -> Window:
    return Window(
        FormattedTextControl(
            FormattedText(
                [
                    ("class:welcome", "Cade Mini  ·  Setup"),
                    ("", "\n"),
                    ("class:status", step),
                ]
            )
        ),
        height=2,
    )


def _cancel_bindings(cancel: Callable[[KeyPressEvent], None]) -> KeyBindings:
    bindings = KeyBindings()
    bindings.add("escape", eager=True)(cancel)
    bindings.add("c-c", eager=True)(cancel)
    return bindings


def select(
    title: str,
    values: list[tuple[str, str]],
    *,
    input: Input | None = None,
    output: Output | None = None,
) -> str | None:
    """Select using arrows and Enter, or return None on Esc/Ctrl+C."""
    if not values:
        return None
    choices: RadioList[str] = RadioList(
        values,
        show_numbers=False,
        select_on_focus=True,
        open_character="",
        select_character="❯",
        close_character="",
        show_cursor=False,
        show_scrollbar=False,
    )
    application: Application[str | None]

    def accept(_event: KeyPressEvent) -> None:
        application.exit(result=choices.current_value)

    def cancel(_event: KeyPressEvent) -> None:
        application.exit(result=None)

    controls = cast(KeyBindings, choices.control.key_bindings)
    controls.add("enter")(accept)
    controls.add("escape", eager=True)(cancel)
    layout = Layout(
        HSplit(
            [
                _heading(title),
                Window(height=1),
                choices,
                Window(
                    FormattedTextControl(
                        FormattedText(
                            [
                                (
                                    "class:status",
                                    "↑/↓ select  ·  Enter confirm  ·  Esc cancel",
                                )
                            ]
                        )
                    ),
                    height=1,
                ),
                Window(height=1),
            ],
            height=Dimension.exact(min(len(values) + 5, 16)),
        )
    )
    layout.focus(choices)
    application = Application(
        layout=layout,
        key_bindings=_cancel_bindings(cancel),
        style=_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
        input=input,
        output=output,
    )
    with preserve_console_input_mode():
        return application.run()


def enter_text(
    title: str,
    *,
    password: bool = False,
    input: Input | None = None,
    output: Output | None = None,
) -> str | None:
    """Inline text editor. The password field masks all typed characters."""
    editor = TextArea(
        height=1,
        prompt="> ",
        multiline=False,
        password=password,
        scrollbar=False,
        style="class:input",
    )
    application: Application[str | None]

    def accept(_event: KeyPressEvent) -> None:
        application.exit(result=editor.text.strip() or None)

    def cancel(_event: KeyPressEvent) -> None:
        application.exit(result=None)

    bindings = _cancel_bindings(cancel)
    bindings.add("enter")(accept)
    layout = Layout(
        HSplit(
            [
                _heading(title),
                Window(height=1),
                editor,
                Window(
                    FormattedTextControl(
                        FormattedText(
                            [("class:status", "Enter confirm  ·  Esc cancel")]
                        )
                    ),
                    height=1,
                ),
                Window(height=1),
            ],
            height=Dimension.exact(6),
        )
    )
    layout.focus(editor)
    application = Application(
        layout=layout,
        key_bindings=bindings,
        style=_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
        input=input,
        output=output,
    )
    with preserve_console_input_mode():
        return application.run()
