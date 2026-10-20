"""Prompt-toolkit inline workspace with native scrollback and durable session state.

All commands go through InteractionService. The terminal owns input, pickers,
approval forms, display state and asynchronous run presentation.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import threading
import time
from typing import Any, cast

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Dimension,
    FormattedTextControl,
    HSplit,
    Layout,
    ScrollOffsets,
    Window,
)
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import RadioList, TextArea

from cade_mini.ai.auth import LoginCancelledError
from cade_mini.ai.models import get_model_reasoning_efforts
from cade_mini.core.loop import LoopEvent, RunResult, UsageEvent
from cade_mini.core.types import ToolCallBlock
from cade_mini.hosts.interaction import InteractionService, UnavailableProvider
from cade_mini.hosts.interaction_question import Question, QuestionTool

from .chrome import TUI_STYLES, compact_path, fit_text, status_line
from .completion import CadeCompleter, CommandArgsSuggester, score
from .inline_renderer import InlineRenderer
from .platform import preserve_console_input_mode
from .rendering import wrap_ansi_lines
from .state import Transcript
from .widgets import TuiCompletionControl, TuiInputLexer, input_prompt

_STYLE = Style.from_dict(TUI_STYLES)

_SHORTCUT_HELP = """Shortcuts
  ?              show this help
  Ctrl+C         clear input or interrupt the active task
  Ctrl+D         exit on double press with an empty prompt
  Ctrl+Q         exit
  Shift+Tab      cycle act → build → plan
  Ctrl+T         toggle thinking details
  Ctrl+O         toggle tool details
  Ctrl+K         searchable command palette
  Ctrl+P         select model
  Ctrl+G         select session
  ! command      run a shell command
  $skill [task]  invoke a skill
  /command       run a slash command
  Up/Down        navigate input history and completion
  Tab            complete commands, skills and @file references
  Enter          send or queue a follow-up task
  Alt+Enter      steer the active task
  Ctrl+J         insert a newline
  Esc Enter      newline when idle, steer while running
  Mouse wheel    browse terminal history
"""


class CadeTui:
    """One foreground interactive terminal with an asyncio-owned agent turn."""

    def __init__(
        self,
        service: InteractionService,
        *,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self.service = service
        if service.approval_router is not None:
            service.approval_router.target = self._approve
        service.question_tool = QuestionTool(self._ask_questions)
        known_context = tuple(
            service.workspace / name
            for name in ("AGENTS.md", "NOTE.md")
            if (service.workspace / name).is_file()
        )
        self.transcript = Transcript(
            service.workspace,
            service.log.session_id,
            tools=tuple(tool.name for tool in service.tools),
            skills=tuple(skill.name for skill in service.skills),
            instructions=known_context,
        )
        self.transcript.restore(service.log)
        if isinstance(service.provider, UnavailableProvider):
            self.transcript.notice(service.provider.reason, error=True)
            self.transcript.notice("Use /login to reconnect this workspace.")
        self._run_task: asyncio.Task[None] | None = None
        self._approval: asyncio.Future[bool] | None = None
        self._approval_call: ToolCallBlock | None = None
        self._exit_armed = 0.0
        self._picker_kind: str | None = None
        self._picker_source: tuple[tuple[str, str], ...] = ()
        self._question_future: asyncio.Future[list[str]] | None = None
        self._question_title = ""
        self._question_options: tuple[tuple[str, str], ...] = ()
        self._question_multiple = False
        self._question_selected: set[str] = set()
        self._question_text_active = False
        self._secret_provider: str | None = None
        self._config_field: str | None = None
        self._config_options: tuple[tuple[str, str], ...] = ()
        self._config_form_active = False
        self._oauth_cancel: threading.Event | None = None
        self._picker_effort = service.provider_config.reasoning_effort
        self._model_efforts: dict[str, str | None] = {}
        self._model_labels: dict[str, str] = {}
        self._last_refresh = 0.0
        self._refresh_scheduled = False
        self._started = time.monotonic()
        self._working_since: float | None = None
        self._branch = self._git_branch()

        self._output_control = FormattedTextControl(text="", focusable=False)
        self._output = Window(
            self._output_control,
            height=lambda: Dimension.exact(self._output_height()),
            wrap_lines=True,
            always_hide_cursor=True,
        )
        try:
            from prompt_toolkit.history import FileHistory

            history_path = service.workspace / ".cade-mini" / "tui_history"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(history_path))
        except OSError:
            history = None
        self._input = TextArea(
            height=lambda: Dimension.exact(self._input_height()),
            prompt=lambda: input_prompt(self._input.text.lstrip().startswith("!")),
            multiline=True,
            completer=CadeCompleter(service),
            lexer=TuiInputLexer(),
            complete_while_typing=True,
            auto_suggest=CommandArgsSuggester(),
            history=history,
            scrollbar=False,
            style="class:input",
        )
        self._input.buffer.on_text_changed += lambda _buffer: self._refresh()
        self._input.buffer.on_completions_changed += lambda _buffer: self._refresh()
        self._completion_panel = ConditionalContainer(
            Window(
                TuiCompletionControl(),
                height=lambda: Dimension.exact(self._completion_height()),
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                style="class:completion-menu",
            ),
            filter=Condition(lambda: self._completion_height() > 0),
        )
        self._picker: RadioList[str] = RadioList(
            [("", "")],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        picker_bindings = cast(KeyBindings, self._picker.control.key_bindings)
        picker_bindings.add("enter")(lambda _event: self._pick())
        picker_bindings.add(
            " ",
            filter=Condition(
                lambda: self._picker_kind == "question" and self._question_multiple
            ),
        )(lambda _event: self._pick())
        picker_bindings.add("escape", eager=True)(lambda _event: self._close_picker())
        picker_bindings.add("left")(lambda _event: self._adjust_picker_effort(-1))
        picker_bindings.add("right")(lambda _event: self._adjust_picker_effort(1))
        picker_bindings.add("tab")(
            lambda _event: self.app.layout.focus(self._picker_search)
        )
        self._picker_search = TextArea(
            height=1,
            prompt="Search › ",
            multiline=False,
            style="class:input",
            scrollbar=False,
        )
        self._picker_search.buffer.on_text_changed += lambda _buffer: (
            self._filter_picker()
        )
        search_bindings = KeyBindings()
        search_bindings.add("enter")(lambda _event: self._pick())
        search_bindings.add("escape", eager=True)(lambda _event: self._close_picker())
        search_bindings.add("up")(lambda _event: self._move_picker(-1))
        search_bindings.add("down")(lambda _event: self._move_picker(1))
        search_bindings.add(
            "left", filter=Condition(lambda: self._picker_kind == "model")
        )(lambda _event: self._adjust_picker_effort(-1))
        search_bindings.add(
            "right", filter=Condition(lambda: self._picker_kind == "model")
        )(lambda _event: self._adjust_picker_effort(1))
        search_bindings.add("pageup")(lambda _event: self._move_picker(-8))
        search_bindings.add("pagedown")(lambda _event: self._move_picker(8))
        search_bindings.add("tab")(lambda _event: self.app.layout.focus(self._picker))
        self._picker_search.control.key_bindings = search_bindings
        self._approvals = RadioList(
            [
                ("yes", "Allow once"),
                ("always", "Allow for this session"),
                ("no", "Deny"),
            ],
            default="no",
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        approval_bindings = cast(KeyBindings, self._approvals.control.key_bindings)
        approval_bindings.add("enter")(lambda _event: self._accept_approval())
        approval_bindings.add("escape", eager=True)(
            lambda _event: self._close_approval()
        )
        approval_bindings.add("pageup")(lambda _event: self._scroll_approval(-1))
        approval_bindings.add("pagedown")(lambda _event: self._scroll_approval(1))
        approval_bindings.add("home")(lambda _event: self._scroll_approval(-(10**6)))
        approval_bindings.add("end")(lambda _event: self._scroll_approval(10**6))
        self._approval_preview = Window(
            FormattedTextControl(text=self._approval_text),
            wrap_lines=True,
            height=lambda: Dimension.exact(self._approval_height()),
            right_margins=[ScrollbarMargin(display_arrows=True)],
        )
        question_text_bindings = KeyBindings()
        question_text_bindings.add("enter")(lambda _event: self._answer_question_text())
        question_text_bindings.add("c-j")(
            lambda _event: self._question_text.buffer.insert_text("\n")
        )
        question_text_bindings.add("escape", eager=True)(
            lambda _event: self._complete_question([])
        )
        self._question_text = TextArea(
            height=3,
            prompt="Answer › ",
            multiline=True,
            scrollbar=False,
            style="class:input",
        )
        self._question_text.control.key_bindings = question_text_bindings
        self._question_heading = Window(
            FormattedTextControl(text=lambda: self._question_title),
            height=lambda: Dimension.exact(self._question_title_height()),
            wrap_lines=True,
            style="class:accent",
        )
        self._question_heading_panel = ConditionalContainer(
            self._question_heading,
            filter=Condition(lambda: self._picker_kind == "question"),
        )
        self._question_text_panel = ConditionalContainer(
            HSplit(
                [
                    Window(
                        FormattedTextControl(text=lambda: self._question_title),
                        height=lambda: Dimension.exact(self._question_title_height()),
                        wrap_lines=True,
                        style="class:accent",
                    ),
                    self._question_text,
                ]
            ),
            filter=Condition(lambda: self._question_text_active),
        )
        self._picker_hint = Window(
            FormattedTextControl(text=self._picker_hint_text),
            height=1,
            style="class:hint",
        )
        self._picker_panel = ConditionalContainer(
            HSplit(
                [
                    self._question_heading_panel,
                    self._picker_search,
                    self._picker,
                    self._picker_hint,
                ],
                height=lambda: Dimension.exact(self._picker_height()),
            ),
            filter=Condition(lambda: self._picker_kind is not None),
        )
        self._approval_panel = ConditionalContainer(
            HSplit(
                [self._approval_preview, self._approvals],
                height=lambda: Dimension.exact(self._approval_height() + 3),
            ),
            filter=Condition(lambda: self._approval is not None),
        )
        self._secret_input = TextArea(
            height=1,
            prompt="API key › ",
            multiline=False,
            password=True,
            style="class:input",
            scrollbar=False,
        )
        self._secret_panel = ConditionalContainer(
            HSplit([Window(height=1), self._secret_input, Window(height=1)]),
            filter=Condition(lambda: self._secret_provider is not None),
        )
        config_bindings = KeyBindings()
        config_bindings.add("enter")(lambda _event: self._save_config_form())
        config_bindings.add("escape", eager=True)(
            lambda _event: self._cancel_config_form()
        )
        self._config_input = TextArea(
            height=1,
            prompt="Value › ",
            multiline=False,
            scrollbar=False,
            style="class:input",
        )
        self._config_input.control.key_bindings = config_bindings
        self._config_form_panel = ConditionalContainer(
            HSplit(
                [
                    Window(
                        FormattedTextControl(
                            text=lambda: f"Configure · {self._config_field or ''}"
                        ),
                        height=1,
                        style="class:accent",
                    ),
                    self._config_input,
                ]
            ),
            filter=Condition(lambda: self._config_form_active),
        )
        self._input_panel = ConditionalContainer(
            HSplit(
                [Window(height=1), self._input, Window(height=1)],
                style="class:input",
            ),
            filter=Condition(
                lambda: (
                    self._approval is None
                    and self._picker_kind is None
                    and self._secret_provider is None
                    and not self._question_text_active
                    and not self._config_form_active
                )
            ),
        )
        self._hint = Window(
            FormattedTextControl(
                text=lambda: fit_text(self._hint_text(), self._width())
            ),
            height=1,
            style="class:hint",
        )
        self._status = Window(
            FormattedTextControl(text=self._status_text),
            height=2,
            style="class:status",
        )
        self.app: Application[None] = Application(
            layout=Layout(
                HSplit(
                    [
                        self._output,
                        self._approval_panel,
                        self._picker_panel,
                        self._question_text_panel,
                        self._secret_panel,
                        self._config_form_panel,
                        self._completion_panel,
                        self._input_panel,
                        ConditionalContainer(
                            self._hint,
                            filter=Condition(lambda: self._hint_height() > 0),
                        ),
                        self._status,
                    ]
                )
            ),
            key_bindings=self._bindings(),
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            enable_page_navigation_bindings=False,
            before_render=lambda _app: self._prepare_frame(),
            style=_STYLE,
            input=input,
            output=output,
        )
        self._renderer = InlineRenderer(self.app.renderer)
        self.app.renderer = self._renderer
        self.app.layout.focus(self._input)

    def _git_branch(self) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.service.workspace), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=False,
                timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def _width(self) -> int:
        return max(20, self.app.output.get_size().columns)

    def _input_height(self) -> int:
        columns = max(1, self._width() - 1)
        prompt_width = fragment_list_width(
            input_prompt(self._input.text.lstrip().startswith("!"))
        )
        lines = 0
        for index, line in enumerate(self._input.text.split("\n")):
            display_width = get_cwidth(line)
            if index == 0:
                display_width += prompt_width
            quotient, remainder = divmod(display_width, columns)
            lines += max(1, quotient + bool(remainder))
        return min(5, lines)

    def _has_modal(self) -> bool:
        return (
            self._approval is not None
            or self._picker_kind is not None
            or self._secret_provider is not None
            or self._question_text_active
            or self._config_form_active
        )

    def _completion_height(self) -> int:
        state = self._input.buffer.complete_state
        if state is None or self._has_modal():
            return 0
        available = self.app.output.get_size().rows - self._input_height() - 9
        return max(0, min(8, len(state.completions), available))

    def _hint_height(self) -> int:
        if self._picker_kind is not None:
            return 0  # The picker already has a dedicated keyboard hint.
        return int(
            self.service.running
            or self._has_modal()
            or self._oauth_cancel is not None
            or self._completion_height() > 0
        )

    def _output_height(self) -> int:
        rows = self.app.output.get_size().rows
        if self._approval is not None:
            panel_height = self._approval_height() + 3
        elif self._picker_kind is not None:
            panel_height = self._picker_height()
        elif self._question_text_active:
            panel_height = self._question_title_height() + 3
        elif self._secret_provider is not None:
            panel_height = 3
        elif self._config_form_active:
            panel_height = 2
        else:
            panel_height = self._input_height() + 2
        available = max(
            1,
            rows
            - 1
            - 2
            - panel_height
            - self._completion_height()
            - self._hint_height(),
        )
        return min(available, self.transcript.line_count(self._width()) + 1)

    def _question_title_height(self) -> int:
        width = max(20, self._width() - 4)
        lines = (len(self._question_title) + width - 1) // width
        return max(1, min(5, lines))

    def _picker_height(self) -> int:
        return min(12, len(self._picker.values) + 2) + (
            self._question_title_height() if self._picker_kind == "question" else 0
        )

    def _approval_height(self) -> int:
        limit = (
            max(3, self.app.output.get_size().rows - 11)
            if self.transcript.tools_expanded
            else 5
        )
        visible = wrap_ansi_lines(
            self._approval_text().splitlines(), max(20, self._width() - 4)
        )
        return min(limit, max(2, len(visible)))

    def _approval_text(self) -> str:
        if self._approval_call is None:
            return ""
        call = self._approval_call
        if self.transcript.tools_expanded:
            lines: list[str] = []
            for key, value in call.args.items():
                lines.append(f"{key}:")
                if isinstance(value, str):
                    lines.extend("  " + line for line in value.splitlines() or [""])
                else:
                    lines.extend(
                        "  " + line
                        for line in json.dumps(
                            value, ensure_ascii=False, indent=2
                        ).splitlines()
                    )
            detail = "\n".join(lines)
        else:
            detail = json.dumps(call.args, ensure_ascii=False, indent=2)
            if len(detail) > 360:
                detail = (
                    detail[:360]
                    + f"\n… {len(detail) - 360} chars hidden (Ctrl+O to expand)"
                )
        return f"Approve {call.name}?\n{detail}"

    def _status_text(self) -> FormattedText:
        service = self.service
        width = self._width()
        project = compact_path(service.workspace)
        if self._branch:
            project += f" ({self._branch})"
        last = service.last_usage
        context = (
            f"context: {last['input']:,} / {service.budget.context_window:,}"
            if last
            else f"context: {service.budget.context_window:,}"
        )
        if last and width >= 90:
            context += f"  in {last.get('input', 0):,} · out {last.get('output', 0):,}"
        first = status_line(
            project,
            context,
            width,
            keep_left_end=True,
            left_style="class:status-accent",
        )
        status = f"● Ready · {service.policy.mode}"
        if self._working_since is not None:
            elapsed = time.monotonic() - self._working_since
            symbol = ("◐", "◓", "◑", "◒")[int(elapsed * 5) % 4]
            status = f"{symbol} Running {elapsed:.0f}s  {status}"
        elif self._approval is not None:
            status = "Awaiting approval"
        elif self._question_future is not None:
            status = "Awaiting answer"
        elif isinstance(service.provider, UnavailableProvider):
            status = "Sign in required"
        elif service.running:
            status = f"● Running  {status}"
        model = service.provider.model
        effort = service.provider_config.reasoning_effort
        if effort:
            model += f" · {effort}"
        second = status_line(
            status,
            model,
            width,
            left_style="class:status-mode",
            right_style="class:status",
        )
        return FormattedText([*first, ("", "\n"), *second])

    def _hint_text(self) -> str:
        if self._config_form_active:
            return " Enter save setting · Esc back to settings "
        if self._question_text_active:
            return " Enter answer · Ctrl+J newline · Esc skip this question "
        if self._secret_provider is not None:
            return " Enter save API key · Esc cancel · input hidden from history "
        if self._oauth_cancel is not None:
            return " Authorization in progress · Ctrl+C cancel "
        if self._approval is not None:
            return " Enter choose · Esc deny · Ctrl+O expand · PgUp/PgDn scroll "
        if self._picker_kind is not None:
            return " ↑↓ select · Enter confirm · Esc cancel · ←→ effort (models) "
        if self._completion_height() > 0:
            return " ↑↓ choose · Enter accept · Esc close "
        if isinstance(self.service.provider, UnavailableProvider):
            return " /login to reconnect · Ctrl+K commands · Ctrl+P models "
        if self.service.running:
            return f" Enter {self.service.busy_mode} · Alt+Enter steer · Ctrl+C stop · Ctrl+J newline "
        return (
            " Enter send · Ctrl+K commands · Ctrl+P model · Ctrl+G sessions"
            " · Ctrl+O tools · Ctrl+T thinking "
        )

    def _picker_hint_text(self) -> str:
        if self._picker_kind == "question":
            return (
                " Enter/Space toggle · choose Done to confirm · Esc skip"
                if self._question_multiple
                else " Enter choose · Esc skip · Custom answer available"
            )
        if self._picker_kind == "model":
            model = str(self._picker.current_value)
            level = self._model_efforts.get(model)
            if level is not None:
                return "  ↑↓ model · ←→ effort · Enter apply · Esc cancel"
            return "  ↑↓ model · Enter apply · Esc cancel"
        return "  ↑↓ select · Enter confirm · Esc cancel"

    def _prepare_frame(self) -> None:
        self.transcript.set_compact_welcome(self.app.output.get_size().rows < 28)
        self._output_control.text = self._renderer.prepare(
            self.transcript.ansi_lines(self._width()),
            self._output_height(),
        )
        if self.app.is_done:
            self._renderer.finish()

    def _refresh(self) -> None:
        self.app.invalidate()

    def _refresh_stream(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh >= 0.06:
            self._last_refresh = now
            self._refresh_scheduled = False
            self._refresh()
        elif not self._refresh_scheduled:
            self._refresh_scheduled = True
            loop = self.app.loop
            if loop is not None:
                loop.call_later(0.06, self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_scheduled = False
        self._last_refresh = time.monotonic()
        self._refresh()

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        bindings.add("enter")(self._enter)
        bindings.add("c-j")(self._newline)
        try:
            bindings.add("s-enter")(self._newline)
        except ValueError:
            pass
        bindings.add("escape", "enter")(self._alt_enter)
        bindings.add("s-tab", filter=Condition(lambda: not self.service.running))(
            lambda _event: self._schedule(self._cycle_mode())
        )
        bindings.add("c-o", eager=True)(lambda _event: self._toggle_tools())
        bindings.add("c-t", eager=True)(lambda _event: self._toggle_thinking())
        bindings.add("c-c", eager=True)(self._cancel)
        bindings.add("c-d")(self._eof)
        bindings.add("c-q")(lambda _event: self._exit())
        palette_ready = Condition(
            lambda: (
                not self.service.running
                and self._picker_kind is None
                and self._approval is None
                and self._question_future is None
                and self._secret_provider is None
                and not self._config_form_active
            )
        )
        bindings.add("c-k", filter=palette_ready)(
            lambda _event: self._open_picker("commands")
        )
        bindings.add("c-p", filter=palette_ready)(
            lambda _event: self._open_picker("model")
        )
        bindings.add("c-g", filter=palette_ready)(
            lambda _event: self._open_picker("sessions")
        )
        bindings.add(
            "?",
            filter=palette_ready & Condition(lambda: not self._input.text),
        )(lambda _event: self._show_shortcuts())
        return bindings

    def _show_shortcuts(self) -> None:
        self.transcript.notice(_SHORTCUT_HELP.rstrip())
        self._refresh()

    def _schedule(self, coroutine: Any) -> None:
        self.app.create_background_task(coroutine)

    def _newline(self, _event: Any) -> None:
        self._input.buffer.insert_text("\n")

    def _alt_enter(self, _event: Any) -> None:
        value = self._input.text.strip()
        if not value:
            self._newline(_event)
            return
        if not self.service.running:
            self._newline(_event)
            return
        self._input.buffer.reset(append_to_history=True)
        self.service.steering.append(self.service.expand(value))
        self.transcript.notice("Guidance queued for the next model step")
        self._refresh()

    def _enter(self, _event: Any) -> None:
        if self._secret_provider is not None:
            self._save_api_key()
            return
        if self._approval is not None:
            self._accept_approval()
            return
        if self._picker_kind is not None:
            self._pick()
            return
        complete = self._input.buffer.complete_state
        if complete is not None and complete.completions:
            self._input.buffer.go_to_completion(complete.complete_index or 0)
            self._input.buffer.complete_state = None
            self._refresh()
            return
        value = self._input.text.strip()
        if not value:
            return
        self._input.buffer.reset(append_to_history=True)
        if self.service.running and not (
            value.startswith(
                ("/btw ", "/steer ", "/queue ", "/stop", "/context", "/help")
            )
        ):
            if value.startswith(("/", "!")):
                self._input.text = value
                self.transcript.notice(
                    "Run this command when the current task finishes."
                )
            else:
                if self.service.busy_mode == "steer":
                    self.service.steering.append(self.service.expand(value))
                    self.transcript.notice("Guidance queued.")
                else:
                    if self.service.busy_mode == "interrupt" and self.service.cancel:
                        self.service.cancel.cancel("Replaced by a new user task")
                    self.service.pending.append(value)
                    self.transcript.notice(
                        "Stopping; task queued."
                        if self.service.busy_mode == "interrupt"
                        else "Follow-up queued."
                    )
            self._refresh()
            return
        self._schedule(self._submit(value))

    async def _submit(self, value: str) -> None:
        try:
            if value.startswith("/"):
                if value.startswith("/tool ") and value.strip() != "/tool list":
                    await self._invoke_tool(value[len("/tool ") :])
                else:
                    await self._dispatch(value)
                return
            if value.startswith("!"):
                await self._run_shell(value[1:].strip())
                return
            await self._launch(value)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self.transcript.notice(str(exc), error=True)
            self._refresh()

    async def _dispatch(self, value: str) -> None:
        if self.service.running and not value.startswith(
            (
                "/btw",
                "/steer",
                "/queue",
                "/stop",
                "/context",
                "/help",
            )
        ):
            self.transcript.notice("This command requires an idle session.", error=True)
            self._refresh()
            return
        result = await self.service.command(value)
        if result.changed_session:
            self.transcript.restore(self.service.log)
        if result.lines:
            self.transcript.notice("\n".join(result.lines))
        if result.login_provider:
            self._begin_login(result.login_provider)
        if result.config_field:
            self._edit_config_field(result.config_field)
        if result.draft:
            self._input.text = result.draft
        if result.picker is not None:
            self._open_picker(result.picker)
        if result.side_question:
            self._schedule(self._side_question(result.side_question))
        if result.task:
            await self._launch(result.task)
        if result.quit:
            self._exit()
        self._refresh()

    def _model_label(self, name: str) -> str:
        label = self._model_labels[name]
        active = (
            f"{self.service.provider_config.provider}/{self.service.provider.model}"
        )
        if name == active:
            label += " · current"
        level = self._model_efforts.get(name)
        if level is not None:
            label += f"    ‹ {level} ›"
        return label

    def _open_picker(self, kind: str) -> None:
        if kind == "question":
            choices = tuple(
                (
                    name,
                    ("☑ " if name in self._question_selected else "☐ ")
                    + name
                    + (f" · {description}" if description else ""),
                )
                for name, description in self._question_options
            )
            choices = (
                *choices,
                ("__free__", "Type your own answer"),
            )
            if self._question_multiple:
                choices = (*choices, ("__done__", "Done · confirm selections"))
        elif kind == "config_value":
            choices = self._config_options
        else:
            choices = self.service.choices(kind)
        if kind == "model":
            if not choices:
                self.transcript.notice(
                    "No available models. Sign in with /login or configure OPENAI_API_KEY."
                )
                return
            active = (
                f"{self.service.provider_config.provider}/{self.service.provider.model}"
            )
            self._model_labels = dict(choices)
                self._model_efforts = {}
            for name, _description in choices:
                levels = get_model_reasoning_efforts(name.split("/", 1)[-1])
                if not levels:
                    self._model_efforts[name] = None
                    continue
                preferred = (
                    self.service.provider_config.reasoning_effort
                    if name == active
                    else "medium"
                )
                self._model_efforts[name] = (
                    preferred
                    if preferred in levels
                    else "high"
                    if "high" in levels
                    else levels[0]
                )
            choices = tuple(
                (name, self._model_label(name)) for name in self._model_labels
            )
        if not choices:
            self.transcript.notice(f"No choices for {kind}")
            return
        self._picker_kind = kind
        self._picker_source = tuple(choices)
        self._picker_effort = self.service.provider_config.reasoning_effort
        self._picker_search.buffer.reset()
        self._filter_picker()
        if kind == "model":
            chosen = (
                f"{self.service.provider_config.provider}/{self.service.provider.model}"
            )
            for index, (name, _) in enumerate(self._picker.values):
                if name == chosen:
                    self._picker._selected_index = index
                    self._picker.current_value = chosen
                    break
        self.app.layout.focus(self._picker_search)
        self._refresh()

    def _filter_picker(self) -> None:
        if self._picker_kind is None:
            return
        query = self._picker_search.text.strip()
        if not query:
            # Preserve configured/recommended order until the user searches.
            values = list(self._picker_source)
        else:
            ranked: list[tuple[tuple[int, int, int], tuple[str, str]]] = []
            for value, description in self._picker_source:
                rank = score(query, value + " " + description)
                if rank is not None:
                    ranked.append((rank, (value, description)))
            ranked.sort(key=lambda item: item[0])
            values = [choice for _, choice in ranked]
        self._picker.values = values or [("", "No matches")]
        self._picker._selected_index = 0
        self._picker.current_value = self._picker.values[0][0]
        self._refresh()

    def _move_picker(self, delta: int) -> None:
        if self._picker_kind is None:
            return
        index = max(
            0, min(len(self._picker.values) - 1, self._picker._selected_index + delta)
        )
        self._picker._selected_index = index
        self._picker.current_value = self._picker.values[index][0]
        self._refresh()

    def _close_picker(self, *, cancel_question: bool = True) -> None:
        is_question = self._picker_kind == "question"
        is_config_value = self._picker_kind == "config_value"
        self._picker_kind = None
        self._picker_source = ()
        self._picker_search.buffer.reset()
        self.app.layout.focus(self._input)
        if is_question and cancel_question:
            self._complete_question([])
        elif is_config_value:
            self._config_field = None
            self._config_options = ()
            self._open_picker("config")
        self._refresh()

    def _adjust_picker_effort(self, step: int) -> None:
        if self._picker_kind != "model":
            return
        name = str(self._picker.current_value)
        if name not in self._model_efforts:
            return
        levels = get_model_reasoning_efforts(name.split("/", 1)[-1])
        current = self._model_efforts[name]
        if not levels or current not in levels:
            return
        next_index = max(0, min(len(levels) - 1, levels.index(current) + step))
        self._model_efforts[name] = levels[next_index]
        selection = self._picker.current_value
        self._picker_source = tuple(
            (item, self._model_label(item)) for item in self._model_labels
        )
        self._filter_picker()
        for index, (item, _) in enumerate(self._picker.values):
            if item == selection:
                self._picker._selected_index = index
                self._picker.current_value = selection
                break
        self._refresh()

    def _pick(self) -> None:
        kind = self._picker_kind
        choice = self._picker.current_value
        if kind is None or not choice:
            return
        if kind == "question":
            if choice == "__free__":
                self._close_picker(cancel_question=False)
                self._question_text_active = True
                self._question_text.buffer.reset()
                self.app.layout.focus(self._question_text)
            elif choice == "__done__":
                self._complete_question(
                    [
                        name
                        for name, _ in self._question_options
                        if name in self._question_selected
                    ]
                )
            elif self._question_multiple:
                if choice in self._question_selected:
                    self._question_selected.remove(choice)
                else:
                    self._question_selected.add(choice)
                current_filter = self._picker_search.text
                self._open_picker("question")
                self._picker_search.text = current_filter
            else:
                self._complete_question([str(choice)])
            self._refresh()
            return
        if kind == "config_value":
            field = self._config_field
            self._picker_kind = None
            self._picker_source = ()
            self._picker_search.buffer.reset()
            self._config_field = None
            self._config_options = ()
            if field is not None:
                try:
                    self.transcript.notice(
                        self.service.settings.save(field, str(choice))
                    )
                except (OSError, ValueError) as exc:
                    self.transcript.notice(str(exc), error=True)
            self._open_picker("config")
            return
        effort = self._model_efforts.get(str(choice))
        self._close_picker()
        if kind == "model":

            async def switch() -> None:
                try:
                    self.transcript.notice(
                        self.service.select_model(str(choice), effort=effort)
                    )
                except (RuntimeError, ValueError) as exc:
                    self.transcript.notice(str(exc), error=True)
                self._refresh()

            self._schedule(switch())
            return
        prefix = {
            "sessions": "/resume",
            "fork": "/fork",
            "tree": "/tree",
            "mode": "/mode",
            "effort": "/effort",
            "permissions": "/permissions",
            "queue": "/queue",
        }.get(kind)
        if kind == "mode":
            self.service.policy.set_mode(str(choice))
        elif prefix:
            self._schedule(self._submit(f"{prefix} {choice}"))
        elif kind == "commands":
            self._input.text = str(choice)
        elif kind == "config":
            self._edit_config_field(str(choice))
        elif kind == "login":
            self._begin_login(str(choice))
        elif kind == "logout":
            self._schedule(self._submit(f"/logout {choice}"))
        elif kind == "skill":
            self._input.text = "$" + str(choice) + " "
        elif kind == "tool":
            self._input.text = "/tool " + str(choice) + " "
        self._refresh()

    def _edit_config_field(self, field: str) -> None:
        options = self.service.settings.edit_options(field)
        self._config_field = field
        self._config_options = options
        if not options and field in {"provider", "model", "reasoning_effort"}:
            self._config_field = None
            self.transcript.notice(
                "No configured choices for this setting. Use /login to connect "
                "a provider or select a supported model.",
                error=True,
            )
            self._open_picker("config")
            return
        if options:
            self._open_picker("config_value")
            return
        draft = self.service.settings.edit_command(field)
        self._config_input.text = draft.split("=", 1)[1]
        self._config_form_active = True
        self.app.layout.focus(self._config_input)
        self._refresh()

    def _cancel_config_form(self) -> None:
        self._config_form_active = False
        self._config_field = None
        self._config_input.buffer.reset()
        self.app.layout.focus(self._input)
        self._open_picker("config")

    def _save_config_form(self) -> None:
        field = self._config_field
        if field is None:
            return
        try:
            result = self.service.settings.save(field, self._config_input.text)
        except (OSError, ValueError) as exc:
            self.transcript.notice(str(exc), error=True)
            self._refresh()
            return
        self.transcript.notice(result)
        self._cancel_config_form()

    def _begin_login(self, provider: str) -> None:
        if provider == "chatgpt":
            self._schedule(self._login_chatgpt())
        elif provider == "openai":
            self._secret_provider = provider
            self._secret_input.buffer.reset()
            self.app.layout.focus(self._secret_input)
            self._refresh()
        else:
            self.transcript.notice(f"Unknown provider: {provider}", error=True)

    async def _ask_questions(self, questions: tuple[Question, ...]) -> list[list[str]]:
        """Suspend the model tool until the active TUI collects every answer."""
        answers: list[list[str]] = []
        for question in questions:
            if self._question_future is not None:
                raise RuntimeError("Another question is already pending")
            self._question_future = asyncio.get_running_loop().create_future()
            self._question_title = (
                f"{question.header} · {question.text}"
                if question.header
                else question.text
            )
            self._question_options = question.options
            self._question_multiple = question.multiple
            self._question_selected.clear()
            self.transcript.notice("Question: " + self._question_title)
            if question.options:
                self._open_picker("question")
            else:
                self._question_text_active = True
                self._question_text.buffer.reset()
                self.app.layout.focus(self._question_text)
                self._refresh()
            answer = await self._question_future
            self._question_future = None
            answers.append(answer)
            self.transcript.notice(
                "Answered: " + (", ".join(answer) if answer else "skipped")
            )
        self._refresh()
        return answers

    def _answer_question_text(self) -> None:
        value = self._question_text.text.strip()
        self._complete_question([value] if value else [])

    def _complete_question(self, answers: list[str]) -> None:
        pending = self._question_future
        self._question_text_active = False
        if self._picker_kind == "question":
            self._close_picker(cancel_question=False)
        self._question_text.buffer.reset()
        if pending is not None and not pending.done():
            pending.set_result(answers)
        self.app.layout.focus(self._input)
        self._refresh()

    def _save_api_key(self) -> None:
        provider = self._secret_provider
        key = self._secret_input.text
        self._secret_input.buffer.reset()
        self._secret_provider = None
        self.app.layout.focus(self._input)
        try:
            if provider is not None:
                self.transcript.notice(self.service.auth.save_api_key(provider, key))
                if self.service.provider_config.provider == provider:
                    self.service.select_model(self.service.provider_config.model)
        except (RuntimeError, ValueError) as exc:
            self.transcript.notice(str(exc), error=True)
        finally:
            key = ""
        self._refresh()

    async def _login_chatgpt(self) -> None:
        cancel = threading.Event()
        self._oauth_cancel = cancel
        loop = asyncio.get_running_loop()

        def notify(message: str) -> None:
            def display() -> None:
                self.transcript.notice(message)
                self._refresh()

            loop.call_soon_threadsafe(display)

        self.transcript.notice("Starting ChatGPT authorization…")
        self._refresh()
        try:
            result = await self.service.auth.login_chatgpt(notify, cancel)
            self.transcript.notice(result)
            if self.service.provider_config.provider == "chatgpt":
                self.service.select_model(self.service.provider_config.model)
        except (
            OSError,
            RuntimeError,
            ValueError,
            TimeoutError,
            LoginCancelledError,
        ) as exc:
            self.transcript.notice(
                f"Login failed: {type(exc).__name__}: {exc}", error=True
            )
        finally:
            self._oauth_cancel = None
            self._refresh()

    def _toggle_tools(self) -> None:
        self.transcript.toggle_tools()
        if self._approval is not None:
            self._approval_preview.vertical_scroll = 0
        self._refresh()

    def _scroll_approval(self, pages: int) -> None:
        if self._approval is None:
            return
        height = self._approval_height()
        physical_lines = wrap_ansi_lines(
            self._approval_text().splitlines(), max(20, self._width() - 4)
        )
        maximum = max(0, len(physical_lines) - height)
        self._approval_preview.vertical_scroll = max(
            0,
            min(
                maximum,
                self._approval_preview.vertical_scroll + pages * max(1, height - 1),
            ),
        )
        self._refresh()

    def _toggle_thinking(self) -> None:
        self.transcript.toggle_thinking()
        self._refresh()

    def _cancel(self, _event: Any) -> None:
        if self._question_future is not None:
            self._complete_question([])
            if self.service.cancel is not None:
                self.service.cancel.cancel("Interrupted by user during a question")
            self.transcript.notice("Stopping current task…")
        elif self._secret_provider is not None:
            self._secret_input.buffer.reset()
            self._secret_provider = None
            self.app.layout.focus(self._input)
        elif self._oauth_cancel is not None:
            self._oauth_cancel.set()
            self.transcript.notice("Cancelling authorization…")
        elif self._approval is not None:
            self._close_approval()
        elif self._picker_kind is not None:
            self._close_picker()
        elif self.service.cancel is not None:
            self.service.cancel.cancel("Interrupted by user")
            self.transcript.notice("Stopping current task…")
        else:
            self._input.buffer.reset()
        self._refresh()

    def _eof(self, _event: Any) -> None:
        if self.service.running or self._input.text:
            return
        now = time.monotonic()
        if now - self._exit_armed <= 2:
            self._exit()
        else:
            self._exit_armed = now
            self.transcript.notice("Press Ctrl+D again to exit.")
            self._refresh()

    def _exit(self) -> None:
        if self._question_future is not None:
            self._complete_question([])
        if self._oauth_cancel is not None:
            self._oauth_cancel.set()
        if self.service.cancel is not None:
            self.service.cancel.cancel("User exited")
        if self._approval is not None:
            self._close_approval()
        self.app.exit()

    async def _approve(self, call: ToolCallBlock) -> bool:
        if self.service.policy.grants.match(call):
            return True
        automatic = self.service.automatic_approver
        if automatic is not None:
            decision = automatic(call)
            if inspect.isawaitable(decision):
                decision = await decision
            return bool(decision)
        loop = asyncio.get_running_loop()
        if self._approval is not None:
            raise RuntimeError("Approval already pending")
        self._approval = loop.create_future()
        self._approval_call = call
        self._approval_preview.vertical_scroll = 0
        self._approvals.current_value = "no"
        self.app.layout.focus(self._approvals)
        self._refresh()
        try:
            return await self._approval
        finally:
            self._approval = None
            self._approval_call = None
            self.app.layout.focus(self._input)
            self._refresh()

    def _accept_approval(self) -> None:
        pending = self._approval
        call = self._approval_call
        if pending is None or pending.done():
            return
        answer = self._approvals.current_value
        if answer == "always" and call is not None:
            self.service.policy.grants.add(call)
        pending.set_result(answer in {"yes", "always"})

    def _close_approval(self) -> None:
        pending = self._approval
        if pending is not None and not pending.done():
            pending.set_result(False)

    def _event(self, event: LoopEvent) -> None:
        if isinstance(event, UsageEvent):
            self.service.last_usage = {
                "input": event.input_tokens,
                "output": event.output_tokens,
            }
        else:
            self.transcript.handle(event)
        self._refresh_stream()

    async def _launch(self, raw: str) -> None:
        if self.service.running:
            self.service.pending.append(raw)
            self.transcript.notice("Follow-up queued")
            return
        try:
            expanded = self.service.expand(raw)
        except (OSError, ValueError) as exc:
            self.transcript.notice(str(exc), error=True)
            self._refresh()
            return
        self.transcript.add_user(raw)
        self.transcript.begin_run()
        self._refresh()
        task = self.app.create_background_task(self._drive_turn(expanded))
        self._run_task = cast(asyncio.Task[None], task)

    async def _drive_turn(self, task: str) -> None:
        result: RunResult | None = None
        self._working_since = time.monotonic()
        ticker = asyncio.create_task(self._working_ticks())
        try:
            result = await self.service.run_task(task, self._event, self._approve)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self.transcript.notice(f"Run failed: {exc}", error=True)
        finally:
            self._working_since = None
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            self.transcript.end_run(result)
            self._refresh()
        if self.service.pending:
            followup = self.service.pending.popleft()
            await self._launch(followup)

    async def _working_ticks(self) -> None:
        """Animate elapsed progress even while the provider stream is silent."""
        while True:
            self._refresh()
            await asyncio.sleep(0.2)

    async def _side_question(self, question: str) -> None:
        entry = self.transcript.start_side(question)
        self._refresh()

        def on_delta(delta: str) -> None:
            self.transcript.side_delta(entry, delta)
            self._refresh_stream()

        try:
            await self.service.side_question(question, on_delta)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self.transcript.notice(f"BTW failed: {exc}", error=True)
        finally:
            self.transcript.finish_side(entry)
            self._refresh()

    async def _invoke_tool(self, tail: str) -> None:
        name, _, arguments = tail.strip().partition(" ")
        if not name:
            self._open_picker("tool")
            return
        if not arguments.strip():
            self._input.text = f"/tool {name} "
            self.transcript.notice("Supply JSON arguments, then press Enter.")
            return
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid tool JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise TypeError("Tool arguments must be a JSON object")
        self.transcript.notice(f"Tool: {name}")
        self._refresh()
        await self.service.execute_tool(
            name, parsed, self._approve, on_event=self._event
        )
        self._refresh()

    async def _run_shell(self, command: str) -> None:
        if not command:
            self.transcript.notice("Usage: !command")
            return
        self.transcript.notice("! " + command)
        self._refresh()
        await self.service.execute_tool(
            "bash", {"command": command}, self._approve, on_event=self._event
        )
        self._refresh()

    async def _cycle_mode(self) -> None:
        """Shift+Tab changes the status mode without adding transcript entries."""
        choices = ("act", "build", "plan")
        current = self.service.policy.mode
        self.service.policy.set_mode(
            choices[(choices.index(current) + 1) % len(choices)]
        )
        self._refresh()

    def run(self) -> int:
        try:
            with preserve_console_input_mode():
                self.app.run()
        finally:
            seconds = time.monotonic() - self._started
            print(
                f"✦ Cade Mini · {seconds:.0f}s · session {self.service.log.session_id}"
            )
            print(f"Resume: cade-mini --resume {self.service.log.session_id}")
        return 0


def run_tui(service: InteractionService) -> int:
    return CadeTui(service).run()
