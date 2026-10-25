"""TUI 应用入口：_CadeTui 主类。"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
from asyncio import TimerHandle
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError, version
from io import StringIO
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from time import perf_counter
from typing import TYPE_CHECKING, cast

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import (
    AnyFormattedText,
    FormattedText,
)
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Dimension,
    HSplit,
    Layout,
    ScrollOffsets,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import CheckboxList, RadioList, TextArea

from cade.app.config import CadeConfig, DEFAULT_CONFIG_NAME
from cade.app.session import ApplicationSession, UnavailableProvider

from .approval import (
    approval_scope_lines,
    full_edit_preview_lines,
    tool_preview_lines,
)
from .bridge import CadeInteractionApp
from .chrome import TUI_STYLES, compact_path, fit_text, status_line, welcome_text
from .command_handlers import (
    COMMAND_NAMES,
    COMMAND_REGISTRY,
    fork_input_text,
    handle_command,
)
from .command_output import TerminalCommandOutput
from .compat import (
    HITL_CHOICES,
    AgentHarnessEvent,
    BusyMessageMode,
    CommandContext,
    ContextSummary,
    HITLResult,
    InteractionState,
    SubmitStatus,
    UserMessage,
    hitl_choices,
    parse_hitl_choice,
)
from .completion import CommandArgsSuggester, CompletionEngine, TuiCompleter
from .config_registry import SettingSpec
from .context_stats import compute_context_summary
from .exit_keys import ExitKeyKind, exit_confirm_hint, exit_confirmed
from .exit_summary import print_exit_summary
from .fullscreen_renderer import FullscreenTranscript
from .inline_renderer import InlineRenderer
from .interaction_compat import (
    CUSTOM_OPTION_LABEL,
    activate_skill,
    available_skill_names,
    current_effort_options,
    current_model_options,
    git_branch_name,
    parse_skill_invocation,
    run_shell_shortcut,
)
from .login_flow import LoginTask, TuiLoginFlow, parse_login_command
from .sessions import (
    select_session_interactively,
)
from .settings import (
    AvailableModelEntry,
    format_model_entry,
    get_available_model_entries,
    is_current_model_entry,
    reasoning_effort_levels_for_transport,
)
from .state import (
    _CommandChoiceRequest,
    _CommandTextRequest,
    _HitlRequest,
    _LogEntry,
    _QuestionChoiceRequest,
    _TuiState,
)
from .widgets import (
    TuiCompletionControl,
    TuiInputLexer,
    tui_input_prompt,
)
from .working import working_status_text

logger = logging.getLogger(__name__)


def installed_version() -> str:
    """读取已安装分发包的版本；源码直跑且未安装时回退到 dev。"""
    try:
        return version("cade")
    except PackageNotFoundError:
        return "dev"


_SHORTCUT_HELP = """Shortcuts
  ?              show this help
  Ctrl+C         clear input; press twice within 500 ms to exit
  Esc            stop the active run; close a picker or form
  Esc Esc        choose a rewind checkpoint when idle with empty input
  Ctrl+D         press Ctrl+D twice to exit when idle (empty input)
  Ctrl+Q         exit
  Shift+Tab      cycle execution mode: ask → build → plan
  Ctrl+T         expand or collapse thinking
  Ctrl+O         expand or collapse tool details
  /verbose       choose Auto, Low, Medium, or High display detail
  ! command      run a bash command
  $skill [task]  invoke a skill
  /command       run a slash command
  Up/Down        recall previous/next input (on first/last line)
  Tab            complete commands, skills, and @file references
  Enter          send; use the selected busy-input mode while running
  Alt+Q          queue the next task while running
  Alt+S          steer the current task while running
  Alt+Enter      steer the current task while running (alias for Alt+S)
  Ctrl+J         insert a newline (Shift+Enter where supported)
  Esc Enter      newline when idle; steer while running
  PageUp/Down    browse transcript in Fullscreen mode
  Mouse wheel    browse terminal history in Inline mode"""

if TYPE_CHECKING:
    from prompt_toolkit.history import History

    from .compat import ApprovalRequest


def run_tui(
    app: CadeInteractionApp | ApplicationSession,
    project_root: Path,
    *,
    resume_latest: bool = False,
    auto_continue: bool = False,
    session_id: str | None = None,
    config_path: Path | None = None,
    initial_prompt: str | None = None,
) -> int:
    from .ptk_patch import suppress_windows_ptk_shutdown_noise

    if isinstance(app, ApplicationSession):
        app = CadeInteractionApp(app)
    suppress_windows_ptk_shutdown_noise()
    try:
        return _CadeTui(
            app,
            project_root,
            resume_latest=resume_latest,
            auto_continue=auto_continue,
            session_id=session_id,
            config_path=config_path,
            initial_prompt=initial_prompt,
        ).run()
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1


class _CadeTui:
    _STREAM_REFRESH_INTERVAL = 0.05
    _EVENT_DRAIN_TIMEOUT = 5.0

    def __init__(
        self,
        app: CadeInteractionApp,
        project_root: Path,
        input: Input | None = None,
        output: Output | None = None,
        *,
        resume_latest: bool = False,
        auto_continue: bool = False,
        session_id: str | None = None,
        config_path: Path | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        self._agent_app = app
        self._project_root = project_root
        self._config_path = config_path
        self._render_mode = CadeConfig.load(
            project_root, config_path
        ).tui_render_mode
        self._fullscreen_transcript = FullscreenTranscript()
        self._store = app.session_store
        self._welcome_version = installed_version()
        self._welcome_tools = tuple(tool.name for tool in app.registry)
        self._welcome_skills = available_skill_names(app)
        instruction_paths = getattr(app, "instruction_paths", None)
        paths = instruction_paths() if callable(instruction_paths) else ()
        self._welcome_instructions: tuple[Path, ...] = (
            tuple(path for path in paths if isinstance(path, Path))
            if isinstance(paths, tuple)
            else ()
        )
        self._interaction_state = InteractionState()
        self._context_summary = ContextSummary()
        self._interaction_state.busy_mode = BusyMessageMode.FOLLOW_UP
        self._startup_notice = ""
        self._state = _TuiState(
            mode=self._interaction_state.mode, project_root=project_root
        )
        self._state.set_verbosity("auto")
        self._restore_startup_session(resume_latest, auto_continue, session_id)
        self._sync_mode_from_agent()
        self._workspace_branch = git_branch_name(project_root)
        self._command_output = TerminalCommandOutput()
        self._login_cancel: Event | None = None
        self._turn_thread: threading.Thread | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        self._login_thread: threading.Thread | None = None
        self._closing = False
        self._login_flow = TuiLoginFlow(
            open_choices=self._open_command_choices,
            open_text=self._open_command_text,
            post=self._post_log,
            run_task=self._run_login_task,
            service=app.service,
            on_connected=lambda provider: self._call_in_ui_thread(
                lambda: self._open_model_selector(provider=provider)
            ),
        )
        self._awaiting_denial_suggestion = False
        self._exit_pending = 0.0
        self._exit_pending_key = ""
        self._last_stream_refresh = 0.0
        self._stream_refresh_handle: TimerHandle | None = None
        self._working_refresh_handle: TimerHandle | None = None
        self._agent_event_queue: Queue[AgentHarnessEvent] = Queue()
        self._agent_event_queue_lock = threading.Lock()
        self._agent_event_drain_lock = threading.Lock()
        self._agent_event_drain_scheduled = False

        # ── UI 组件 ──
        self._output_control = FormattedTextControl(text="", focusable=False)
        self._output = Window(
            self._output_control,
            wrap_lines=True,
            always_hide_cursor=True,
            height=lambda: Dimension.exact(self._output_height()),
        )
        completer = self._make_completer()
        self._input = TextArea(
            text=app.service.log.navigation_draft or "",
            height=lambda: Dimension.exact(self._input_height()),
            prompt=self._input_prompt,
            multiline=True,
            completer=completer,
            lexer=TuiInputLexer(),
            password=Condition(
                lambda: (
                    self._state.pending_command_text is not None
                    and self._state.pending_command_text.secret
                )
            ),
            complete_while_typing=Condition(
                lambda: (
                    self._state.pending_command_text is None
                    or not self._state.pending_command_text.secret
                )
            ),
            auto_suggest=CommandArgsSuggester(completer.command_args),
            history=_tui_history(project_root),
            scrollbar=False,
            style="class:input",
        )
        self._input.buffer.cursor_position = len(self._input.text)
        self._input.buffer.on_text_insert += self._on_input_text_inserted
        self._input.buffer.on_text_changed += self._on_input_layout_changed
        self._input.buffer.on_completions_changed += self._on_input_layout_changed
        self._completion_container = ConditionalContainer(
            Window(
                TuiCompletionControl(),
                height=lambda: Dimension.exact(self._completion_height()),
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                style="class:completion-menu",
            ),
            filter=Condition(lambda: self._completion_height() > 0),
        )
        self._approval_choices = RadioList(
            [(choice, choice) for choice in HITL_CHOICES],
            default=HITL_CHOICES[0],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        approval_bindings = cast(
            KeyBindings, self._approval_choices.control.key_bindings
        )
        approval_bindings.add("enter")(lambda _event: self._accept_approval_choice())
        self._command_choices: RadioList[object] = RadioList(
            [(None, "")],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        command_bindings = cast(KeyBindings, self._command_choices.control.key_bindings)
        command_bindings.add("enter")(lambda _event: self._accept_command_choice())
        command_bindings.add("left")(lambda _event: self._adjust_command_choice(-1))
        command_bindings.add("right")(lambda _event: self._adjust_command_choice(1))
        # Esc 由菜单控件自身消费（eager 立即触发），返回上级或关闭。
        command_bindings.add("escape", eager=True)(
            lambda _event: self._cancel_key(None)
        )
        self._question_choices: RadioList[str] = RadioList(
            [("", "")],
            show_numbers=True,
            select_on_focus=True,
            open_character="",
            select_character="❯",
            close_character="",
            show_cursor=False,
            show_scrollbar=False,
        )
        question_bindings = cast(
            KeyBindings, self._question_choices.control.key_bindings
        )
        question_bindings.add("escape", eager=True)(
            lambda _event: self._cancel_key(None)
        )
        question_bindings.add("enter")(lambda _event: self._accept_question_choice())
        self._question_checkboxes: CheckboxList[str] = CheckboxList(
            [("", "")],
            open_character="",
            select_character="✓",
            close_character="",
        )
        checkbox_bindings = cast(
            KeyBindings, self._question_checkboxes.control.key_bindings
        )
        checkbox_bindings.add("escape", eager=True)(
            lambda _event: self._cancel_key(None)
        )
        checkbox_bindings.add("enter")(lambda _event: self._accept_question_choice())
        self._approval_container = ConditionalContainer(
            HSplit(
                [self._approval_choices],
                height=lambda: Dimension.exact(self._approval_panel_height()),
            ),
            filter=Condition(
                lambda: (
                    self._state.pending_hitl is not None
                    and not self._awaiting_denial_suggestion
                )
            ),
        )
        self._command_container = ConditionalContainer(
            HSplit(
                [
                    self._command_choices,
                    Window(
                        FormattedTextControl(text=self._command_choice_hint_text),
                        height=1,
                    ),
                ],
                height=lambda: Dimension.exact(self._command_panel_height()),
            ),
            filter=Condition(lambda: self._state.pending_command_choice is not None),
        )
        self._question_container = ConditionalContainer(
            HSplit(
                [
                    Window(
                        FormattedTextControl(text=self._question_prompt_text),
                        height=1,
                    ),
                    ConditionalContainer(
                        self._question_choices,
                        filter=Condition(self._single_question_visible),
                    ),
                    ConditionalContainer(
                        self._question_checkboxes,
                        filter=Condition(self._multiple_question_visible),
                    ),
                ],
                height=lambda: Dimension.exact(self._question_panel_height()),
            ),
            filter=Condition(lambda: self._state.pending_question_choice is not None),
        )
        input_visible = Condition(
            lambda: (
                self._state.pending_command_choice is None
                and self._state.pending_question_choice is None
                and (
                    self._state.pending_hitl is None or self._awaiting_denial_suggestion
                )
            )
        )
        self._input_container = ConditionalContainer(
            HSplit(
                [
                    Window(height=1),
                    self._input,
                    Window(height=1),
                ],
                style="class:input",
            ),
            filter=input_visible,
        )
        self._status = Window(
            FormattedTextControl(text=self._status_text),
            height=2,
            style="class:status",
        )
        self._input_hint = Window(
            FormattedTextControl(text=self._input_hint_text),
            height=1,
            style="class:status",
        )
        # ── 应用 ──
        self._application = Application(
            layout=Layout(
                HSplit(
                    [
                        self._output,
                        self._approval_container,
                        self._command_container,
                        self._question_container,
                        self._completion_container,
                        self._input_container,
                        ConditionalContainer(
                            self._input_hint,
                            filter=Condition(lambda: self._input_hint_height() > 0),
                        ),
                        self._status,
                    ],
                ),
            ),
            key_bindings=self._bindings(),
            full_screen=self._render_mode == "fullscreen",
            erase_when_done=True,
            # Inline 保留终端原生滚动；Fullscreen 使用备用屏幕。
            mouse_support=False,
            enable_page_navigation_bindings=False,
            before_render=lambda _app: self._prepare_frame(),
            style=Style.from_dict(TUI_STYLES),
            input=input,
            output=output,
        )

        self._application.timeoutlen = 0.5
        self._application.ttimeoutlen = 0.1
        self._inline_renderer: InlineRenderer | None = None
        if self._render_mode == "inline":
            self._inline_renderer = InlineRenderer(self._application.renderer)
            self._application.renderer = self._inline_renderer

        # ── 连接 Agent 钩子 ──
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            if getattr(agent, "approval_policy", "on-request") == "on-request":
                agent.user_approval_callback = self._approval_callback
            agent.session_id = self._store.session_id
            self._context_summary = compute_context_summary(agent)

        question_tool = next(
            (tool for tool in self._agent_app.registry if tool.name == "question"),
            None,
        )
        if question_tool is None:
            from cade.coding.tools.question import QuestionTool

            question_tool = QuestionTool(self._answer_questions)
            self._agent_app.service.question_tool = question_tool
        elif hasattr(question_tool, "answer"):
            question_tool.answer = self._answer_questions

        if not self._state.log:
            self._state.log.append(_LogEntry("welcome", self._welcome_text()))
        if self._startup_notice:
            self._state.log.append(_LogEntry("system", self._startup_notice))
        if isinstance(self._agent_app.service.provider, UnavailableProvider):
            self._state.log.append(
                _LogEntry("system", self._agent_app.service.provider.reason)
            )
        self._application.layout.focus(self._input)
        self._refresh()
        if initial_prompt is not None:

            def submit_initial_prompt() -> None:
                self._input.text = ""
                self._submit(initial_prompt)

            self._application.pre_run_callables.append(submit_initial_prompt)

    def run(self) -> int:
        started_at = perf_counter()
        try:
            self._application.run()
        finally:
            print_exit_summary(
                self._store,
                perf_counter() - started_at,
                self._project_root,
                self._config_path,
            )
        return 0

    # ── 辅助 ──

    def _make_completer(self) -> TuiCompleter:
        registry = tuple(getattr(self._agent_app, "registry", ()) or ())
        return TuiCompleter(
            CompletionEngine(
                project_root=self._project_root,
                registry=registry,
                command_names=COMMAND_NAMES,
                command_registry=COMMAND_REGISTRY,
                effort_options=lambda: current_effort_options(self._agent_app),
                model_options=lambda: current_model_options(self._agent_app),
                skill_options=lambda: available_skill_names(self._agent_app),
            )
        )

    def _input_prompt(self) -> FormattedText:
        request = self._state.pending_command_text
        if request is not None:
            return FormattedText([("", f"{request.prompt} > ")])
        return FormattedText(
            tui_input_prompt(
                self._awaiting_denial_suggestion,
                self._input.text.startswith("!"),
            )
        )

    def _question_prompt_text(self) -> str:
        request = self._state.pending_question_choice
        if request is None:
            return ""
        hint = (
            " (Space to toggle, Enter to submit)"
            if request.multiple
            else " (Use arrow keys)"
        )
        return f"? {request.prompt}{hint}"

    def _single_question_visible(self) -> bool:
        request = self._state.pending_question_choice
        return request is not None and not request.multiple

    def _multiple_question_visible(self) -> bool:
        request = self._state.pending_question_choice
        return request is not None and request.multiple

    def _question_panel_height(self) -> int:
        request = self._state.pending_question_choice
        if request is None:
            return 0
        available = max(2, self._application.output.get_size().rows - 6)
        return min(len(request.choices) + 1, available)

    def _approval_panel_height(self) -> int:
        if self._state.pending_hitl is None or self._awaiting_denial_suggestion:
            return 0
        return min(
            len(self._approval_choices.values),
            max(1, self._application.output.get_size().rows - 6),
        )

    def _command_panel_height(self) -> int:
        request = self._state.pending_command_choice
        if request is None:
            return 0
        return min(
            len(request.choices) + 1,
            max(2, self._application.output.get_size().rows - 6),
        )

    def _completion_height(self) -> int:
        state = self._input.buffer.complete_state
        if state is None or self._has_pending_interaction():
            return 0
        available = self._application.output.get_size().rows - self._input_height() - 9
        return min(8, len(state.completions), max(0, available))

    # ── 键绑定 ──

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()
        bindings.add("enter")(self._submit_key)
        try:
            bindings.add("s-enter")(self._insert_newline)
        except ValueError:
            pass
        steering = Condition(
            lambda: self._state.running and not self._has_pending_interaction()
        )
        bindings.add("escape", "enter", filter=steering)(self._steer_key)
        bindings.add("escape", "s", filter=steering)(self._steer_key)
        bindings.add("escape", "q", filter=steering)(self._queue_key)
        bindings.add("escape", "enter", filter=~steering)(self._insert_newline)
        bindings.add(
            "s-tab",
            eager=True,
            filter=Condition(
                lambda: not self._state.running and not self._has_pending_interaction()
            ),
        )(self._cycle_mode_key)
        bindings.add("c-j")(self._insert_newline)
        bindings.add("?")(self._show_shortcuts_key)
        # 折叠快捷键必须是应用级绑定：焦点可能在授权列表等非输入控件。
        bindings.add("c-t", eager=True)(self._toggle_thinking_key)
        bindings.add("c-o", eager=True)(self._toggle_tools_key)
        full_screen_history = Condition(
            lambda: (
                self._render_mode == "fullscreen"
                and not self._has_pending_interaction()
            )
        )
        bindings.add("pageup", eager=True, filter=full_screen_history)(
            self._scroll_transcript_up
        )
        bindings.add("pagedown", eager=True, filter=full_screen_history)(
            self._scroll_transcript_down
        )
        bindings.add("c-q")(self._quit_key)
        bindings.add("c-c", eager=True)(self._cancel_key)
        interrupting = Condition(
            lambda: self._state.running and not self._has_pending_interaction()
        )
        # 恢复提交期间立即提示；普通任务先解析 Alt 组合键。
        bindings.add(
            "escape",
            filter=interrupting,
            eager=Condition(
                lambda: (
                    self._recovery_task is not None and not self._recovery_task.done()
                )
            ),
        )(self._interrupt_key)
        bindings.add(
            "escape",
            "escape",
            eager=True,
            filter=Condition(
                lambda: (
                    not self._state.running
                    and not self._input.text
                    and not self._has_pending_interaction()
                )
            ),
        )(self._history_key)
        bindings.add("c-d")(self._eof_key)
        # 表单中的 Esc 立即取消；补全保留 Alt 组合键解析。
        bindings.add(
            "escape",
            eager=Condition(lambda: self._state.pending_command_text is not None),
            filter=Condition(
                lambda: (
                    self._state.pending_command_text is not None
                    or self._input.buffer.complete_state is not None
                )
            ),
        )(self._escape_key)
        return bindings

    def _cycle_mode_key(self, _event: object) -> None:
        """空闲时循环切换模式并保留尚未提交的输入。"""
        modes = ("ask", "build", "plan")
        selected = modes[(modes.index(self._interaction_state.mode) + 1) % len(modes)]
        command = f"/mode {selected}"
        self._run_command(command)

    def _submit_key(self, _event: object) -> None:
        if self._closing:
            return
        buffer = self._input.buffer
        completion = buffer.complete_state
        if (
            completion is not None
            and completion.completions
            and self._completion_height() > 0
        ):
            buffer.go_to_completion(completion.complete_index or 0)
            buffer.complete_state = None
            self._refresh()
            return
        text = self._input.text.strip()
        # 表单输入可能是密钥等敏感内容，只清空不写入历史。
        if self._state.pending_command_text is not None:
            buffer.reset()
            self._submit_command_text(text)
            return
        if self._awaiting_denial_suggestion:
            buffer.reset()
            self._finish_denial(text)
            return
        if not text:
            return
        self._exit_pending = 0.0
        self._exit_pending_key = ""
        buffer.reset(append_to_history=True)
        if self._state.running:
            if self._state.running and _is_live_command(text):
                self._show_command(text)
                self._run_command(text, preserve_running=True)
            elif self._state.running and not text.startswith(("/", "!", "$")):
                self._submit_busy_message(text)
            else:
                self._input.text = text
                self._input.buffer.cursor_position = len(text)
                self._state.log.append(
                    _LogEntry(
                        "system", "Run this command after the current task finishes."
                    )
                )
                self._refresh()
            return
        self._submit(text)

    def _steer_key(self, _event: object) -> None:
        """将普通输入作为当前任务的引导。"""
        self._submit_busy_input(BusyMessageMode.STEER)

    def _queue_key(self, _event: object) -> None:
        """将普通输入排为后续任务，不受 Enter 策略影响。"""
        self._submit_busy_input(BusyMessageMode.FOLLOW_UP)

    def _submit_busy_input(self, mode: BusyMessageMode) -> None:
        """显式快捷键只提交运行期间的普通消息。"""
        if self._closing or not self._state.running or self._has_pending_interaction():
            return
        text = self._input.text.strip()
        if not text:
            return
        if text.startswith(("/", "!", "$")):
            action = (
                "steer the current task"
                if mode is BusyMessageMode.STEER
                else "queue the next task"
            )
            self._state.log.append(_LogEntry("system", f"Type a message to {action}."))
            self._refresh()
            return
        self._input.buffer.reset(append_to_history=True)
        self._submit_busy_message(text, mode)

    def _submit_busy_message(
        self, text: str, mode: BusyMessageMode | None = None
    ) -> None:
        """普通 Enter 使用当前策略，显式快捷键传入固定策略。"""
        outcome = self._agent_app.agent.submit_busy_message(
            UserMessage(content=text),
            mode or self._interaction_state.busy_mode,
            display_text=text,
        )
        self._state.add_user(text)
        if outcome.status is SubmitStatus.STEER_ACCEPTED:
            self._state.log.append(
                _LogEntry("system", "Added guidance to the current task.")
            )
        elif outcome.status is SubmitStatus.FOLLOW_UP_QUEUED:
            self._state.log.append(
                _LogEntry(
                    "system",
                    "Queued for the next task.",
                )
            )
        elif outcome.status is SubmitStatus.INTERRUPT_REQUESTED:
            self._state.log.append(
                _LogEntry(
                    "system", "Stopping the current task; your message runs next."
                )
            )
        elif outcome.status is SubmitStatus.INJECT_QUEUED:
            self._state.log.append(
                _LogEntry("system", "The task is finishing; your guidance runs next.")
            )
        self._refresh()

    def _input_hint_text(self) -> str:
        if self._has_pending_interaction():
            text = "Enter confirm · Esc back · Ctrl+C cancel"
        elif self._completion_height():
            text = "↑/↓ choose · Enter accept · Esc close"
        elif self._state.running:
            action = {
                BusyMessageMode.FOLLOW_UP: "queue",
                BusyMessageMode.STEER: "steer",
                BusyMessageMode.INTERRUPT: "replace",
            }[self._interaction_state.busy_mode]
            text = (
                f"Enter {action} · Alt+Q queue · Alt+S steer · Esc stop · Ctrl+C clear"
            )
        else:
            text = "Enter send · Shift+Tab mode · Ctrl+J newline · @ files · / commands · cade --continue"
        return fit_text(text, self._output_width())

    def _input_hint_height(self) -> int:
        """欢迎区已经说明空闲操作，底部只在交互状态变化时补充提示。"""
        return int(
            self._state.running
            or self._has_pending_interaction()
            or self._input.buffer.complete_state is not None
        )

    def _insert_newline(self, event: object) -> None:
        buffer = getattr(event, "current_buffer", None)
        if buffer is not None:
            buffer.insert_text("\n")

    def _show_shortcuts_key(self, event: object) -> None:
        if self._input.text:
            buffer = getattr(event, "current_buffer", None)
            if buffer is not None:
                buffer.insert_text("?")
            return
        self._state.log.append(_LogEntry("system", _SHORTCUT_HELP))
        self._refresh()

    def _toggle_thinking_key(self, _event: object) -> None:
        self._state.toggle_thinking()
        self._refresh()

    def _toggle_tools_key(self, _event: object) -> None:
        self._state.toggle_tools()
        self._refresh()

    def _scroll_transcript_up(self, _event: object) -> None:
        """全屏视图按页向上查看会话历史。"""
        self._scroll_transcript(max(1, self._output_height() - 2))

    def _scroll_transcript_down(self, _event: object) -> None:
        """全屏视图按页向下查看会话历史。"""
        self._scroll_transcript(-max(1, self._output_height() - 2))

    def _scroll_transcript(self, delta: int) -> None:
        self._fullscreen_transcript.scroll(
            delta,
            self._state.line_count(self._output_width()),
            self._output_height(),
        )
        self._refresh()

    def _escape_key(self, _event: object) -> None:
        """Esc 关闭补全或文本表单，不改变待提交的输入。"""
        if self._state.pending_command_text is not None:
            self._cancel_pending_command_text()
            return
        if self._input.buffer.complete_state is not None:
            self._input.buffer.cancel_completion()
            self._refresh()
            return

    def _cancel_pending_command_text(self) -> None:
        """关闭文本表单并执行其取消回调。"""
        request = self._state.pending_command_text
        if request is None:
            return
        self._state.pending_command_text = None
        self._input.buffer.complete_state = None
        if request.on_cancel is not None:
            request.on_cancel()
        self._refresh()

    def _quit_key(self, _event: object) -> None:
        if self._state.pending_question_choice is not None:
            self._complete_question_choice(None)
        if self._state.pending_command_text is not None:
            request = self._state.pending_command_text
            self._state.pending_command_text = None
            if request.on_cancel is not None:
                request.on_cancel()
        if self._state.pending_hitl is not None:
            self._finish_denial("")
        self._finish_exit()

    def _on_input_text_inserted(self, _buffer: object) -> None:
        """输入新内容即取消待确认的退出状态。"""
        self._input.buffer.complete_state = None
        self._exit_pending = 0.0
        self._exit_pending_key = ""

    def _on_input_layout_changed(self, _buffer: object) -> None:
        """输入和异步补全改变占用高度后重新分配可见区域。"""
        if hasattr(self, "_application"):
            self._refresh()

    def _cancel_key(self, _event: object) -> None:
        if self._state.pending_question_choice is not None:
            self._complete_question_choice(None)
            return
        if self._state.pending_command_choice is not None:
            request = self._state.pending_command_choice
            self._state.pending_command_choice = None
            self._application.layout.focus(self._input)
            self._input.buffer.complete_state = None
            if request.on_cancel is not None:
                request.on_cancel()
            self._refresh()
            return
        if self._state.pending_command_text is not None:
            self._cancel_pending_command_text()
            return
        if self._state.pending_hitl is not None:
            self._finish_denial("")
        now = perf_counter()
        if exit_confirmed(
            self._exit_pending, self._exit_pending_key, now, ExitKeyKind.INTERRUPT
        ):
            self._finish_exit()
            return
        if self._input.text:
            # reset 同时结束历史浏览，下次 ↑ 从最新一条开始。
            self._input.buffer.reset()
            self._arm_exit(now, ExitKeyKind.INTERRUPT)
            return
        self._arm_exit(now, ExitKeyKind.INTERRUPT)

    def _history_key(self, _event: object) -> None:
        """空闲且输入为空时，双击 Esc 打开回溯检查点。"""
        self._submit("/rewind")

    def _interrupt_key(self, _event: object) -> None:
        """中断当前任务，不清空草稿，也不进入退出确认。"""
        self._exit_pending = 0.0
        self._exit_pending_key = ""
        if self._recovery_task is not None and not self._recovery_task.done():
            self._state.log.append(
                _LogEntry(
                    "system",
                    "Recovery is committing; wait for completion. Esc does not interrupt file restoration.",
                )
            )
            self._refresh()
            return
        if self._login_cancel is not None:
            self._login_cancel.set()
            self._state.log.append(_LogEntry("stop", "Cancelling login..."))
        else:
            service = self._agent_app.service
            queued = [*service.steering, *service.pending]
            service.steering.clear()
            service.pending.clear()
            if queued:
                self._input.text = "\n\n".join([*queued, self._input.text]).strip()
                self._input.buffer.cursor_position = len(self._input.text)
            if self._agent_app.agent.interrupt("interrupted by user"):
                self._state.log.append(_LogEntry("stop", "[interrupt] stopping run"))
        self._refresh()

    def _eof_key(self, _event: object) -> None:
        """Ctrl+D：空闲且输入为空时参与双击退出，其余场景保持无操作。"""
        now = perf_counter()
        if exit_confirmed(
            self._exit_pending, self._exit_pending_key, now, ExitKeyKind.EOF
        ):
            self._finish_exit()
            return
        if self._state.running or self._input.text:
            return
        if self._has_pending_interaction():
            return
        self._arm_exit(now, ExitKeyKind.EOF)

    def _has_pending_interaction(self) -> bool:
        """是否存在需要用户先处理的菜单、表单或授权请求。"""
        return (
            self._awaiting_denial_suggestion
            or self._state.pending_hitl is not None
            or self._state.pending_command_choice is not None
            or self._state.pending_command_text is not None
            or self._state.pending_question_choice is not None
        )

    def _arm_exit(self, now: float, key: ExitKeyKind) -> None:
        """进入双击退出确认窗口，并提示再次按同一个键。"""
        self._exit_pending = now
        self._exit_pending_key = key.value
        self._state.log.append(_LogEntry("system", exit_confirm_hint(key)))
        self._refresh()

    def _finish_exit(self) -> None:
        """取消后台工作，待其完成日志与快照提交后退出事件循环。"""
        if self._closing:
            return
        self._closing = True
        if self._login_cancel is not None:
            self._login_cancel.set()
        self._agent_app.service.pending.clear()
        self._agent_app.service.steering.clear()
        self._agent_app.agent.interrupt("terminal closed")

        async def shutdown() -> None:
            for worker in (self._turn_thread, self._login_thread):
                if worker is not None:
                    await asyncio.to_thread(worker.join)
            if self._recovery_task is not None:
                await asyncio.shield(self._recovery_task)
            self._application.exit()

        self._application.create_background_task(shutdown())

    # ── 提交 ──

    def _submit(self, text: str) -> None:
        skill_invocation = parse_skill_invocation(text)
        if skill_invocation is not None:
            skill_name, remaining_text = skill_invocation
            activation = activate_skill(
                self._agent_app,
                skill_name,
                mode=self._interaction_state.mode,
            )
            self._state.log.append(_LogEntry("system", activation.message))
            if activation.status not in {"activated", "already_active"}:
                self._refresh()
                return
            if not remaining_text:
                self._refresh()
                return
            text = remaining_text
        if text.startswith("/"):
            self._show_command(text)
            self._run_command(text)
            self._refresh()
            return
        if text.startswith("!"):
            self._show_command(text)
            self._run_shell_shortcut(text)
            self._refresh()
            return

        if isinstance(self._agent_app.service.provider, UnavailableProvider):
            self._input.text = text
            self._state.log.append(
                _LogEntry("error", self._agent_app.service.provider.reason)
            )
            self._refresh()
            return
        self._state.add_user(text)
        self._start_working()
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            token = getattr(agent, "cancellation_token", None)
            if token is not None:
                token.reset()
        self._refresh()
        thread = threading.Thread(
            target=self._run_turn,
            args=(text, text),
            daemon=True,
        )
        self._turn_thread = thread
        thread.start()

    def _run_command(
        self,
        text: str,
        *,
        preserve_running: bool = False,
        recovery_confirmed: bool = False,
    ) -> None:
        """执行斜杠命令；实时命令不得结束正在运行的 agent 回合。"""
        if text in {"/clear", "/new"}:
            self._clear_session()
            return
        if not recovery_confirmed and self._show_native_command_choice(text):
            return

        if not preserve_running:
            self._state.running = True

        def invoke(capture_output: bool) -> tuple[bool, str, bool]:
            def handle() -> tuple[bool, bool]:
                ctx = CommandContext(
                    app=self._agent_app,
                    output=self._command_output,
                    state=self._interaction_state,
                )
                should_exit = handle_command(text, ctx)
                return should_exit, ctx.changed_session

            if not capture_output:
                should_exit, changed_session = handle()
                return should_exit, "", changed_session
            output = StringIO()
            with redirect_stdout(output):
                should_exit, changed_session = handle()
            return should_exit, output.getvalue(), changed_session

        async def run_inline() -> None:
            should_exit = False
            output = ""
            error: str | None = None
            changed_session = False
            try:
                (
                    should_exit,
                    output,
                    changed_session,
                ) = await asyncio.get_running_loop().run_in_executor(None, invoke, True)
            except (OSError, RuntimeError, ValueError) as exc:
                error = f"[error] {exc}"
            finally:
                if preserve_running:
                    if error is not None:
                        self._state.log.append(_LogEntry("error", error))
                    elif output.strip():
                        self._state.log.append(_LogEntry("system", output.rstrip()))
                    self._refresh()
                    self._submit_pending_input()
                else:
                    self._finish_command(
                        text,
                        should_exit,
                        output,
                        error,
                        changed_session=changed_session,
                    )

        if self._application.loop is None:
            asyncio.run(run_inline())
        else:
            task = self._application.create_background_task(run_inline())
            if text.split(maxsplit=1)[0] in {"/undo", "/redo", "/rewind", "/revert"}:
                self._recovery_task = task

    def _show_command(self, text: str) -> None:
        """显示命令输入；需要恢复的输入、状态和结果由各自处理器持久化。"""
        self._state.add_command(text)

    def _set_verbosity(self, level: str) -> None:
        """切换当前终端的显示层级，不修改会话事件。"""
        self._state.set_verbosity(level)
        self._state.log.append(_LogEntry("system", f"Display verbosity: {level}"))
        self._refresh()

    def _show_native_command_choice(self, text: str) -> bool:
        """为需要选择或输入的命令打开 TUI 原生菜单。"""
        command = text.split(maxsplit=1)[0]
        if command == "/verbose":
            parts = text.split()
            if len(parts) == 1:
                self._open_command_choices(
                    [
                        (
                            f"{level} (current)"
                            if level == self._state.verbosity
                            else level,
                            level,
                        )
                        for level in ("auto", "low", "medium", "high")
                    ],
                    lambda selected: self._set_verbosity(str(selected)),
                    describe=lambda level: {
                        "auto": (
                            "Show individual folded calls while running; "
                            "collapse activity after the turn."
                        ),
                        "low": "Show messages; hide thinking and tool transcripts.",
                        "medium": "Show compact thinking and tool summaries.",
                        "high": "Show expanded thinking and tool details.",
                    }[str(level)],
                )
            elif len(parts) == 2 and parts[1] in {"auto", "low", "medium", "high"}:
                self._set_verbosity(parts[1])
            else:
                self._state.log.append(
                    _LogEntry("error", "Usage: /verbose [auto|low|medium|high]")
                )
                self._refresh()
            return True
        if command in {"/quit", "/exit"}:
            self._quit_key(None)
            return True
        if command == "/stop" and self._state.running:
            self._interrupt_key(None)
            return True
        login = parse_login_command(text)
        if login is not None:
            self._login_flow.start(login)
            return True
        if command == "/effort" and len(text.split()) == 1:
            return self._open_effort_selector()
        if (
            command in {"/thinking", "/queue", "/skill", "/logout"}
            and len(text.split()) == 1
        ):
            service = self._agent_app.service
            kind = command[1:]
            if kind == "logout":
                choices = [(name, name) for name in service.auth.store.load_all()]
            else:
                choices = list(service.choices(kind))
            if choices:
                self._open_command_choices(
                    [(label, name) for name, label in choices],
                    lambda selected: self._run_command(f"{command} {selected}"),
                )
            else:
                self._state.log.append(
                    _LogEntry("system", f"No available choices for {command}.")
                )
            return True
        if command == "/auth" and text.split()[1:] == ["logout"]:
            return self._show_native_command_choice("/logout")
        if command == "/skill":
            name = text.partition(" ")[2].strip()
            activation = activate_skill(
                self._agent_app, name, mode=self._interaction_state.mode
            )
            self._state.log.append(_LogEntry("system", activation.message))
            return True
        if command == "/btw" and len(text.split()) == 1:
            self._open_command_text(
                "Ask a side question (Esc to cancel)",
                lambda value: (
                    self._run_command("/btw " + value) if value.strip() else None
                ),
            )
            return True
        if command == "/mode" and len(text.split()) == 1:
            self._open_command_choices(
                [
                    (
                        f"{mode} (current)"
                        if mode == self._interaction_state.mode
                        else mode,
                        mode,
                    )
                    for mode in ("ask", "build", "plan")
                ],
                lambda selected: self._run_command(f"/mode {selected}"),
            )
            return True
        if command == "/model":
            query = text.partition(" ")[2].strip()
            choices = self._agent_app.service.choices("model")
            bare = [name for name, _ in choices if query == name.partition("/")[2]]
            if "/" not in query and len(bare) == 1:
                self._run_command("/model " + bare[0])
                return True
            if not query or not any(
                query == name or query == name.partition("/")[2] for name, _ in choices
            ):
                self._open_model_selector(query=query)
                return True
        if command == "/permissions" and len(text.split()) == 1:
            self._open_command_choices(
                [
                    ("Show permission status", "/permissions list"),
                    ("Clear session permissions", "/permissions clear"),
                ],
                lambda selected: self._run_command(str(selected)),
            )
            return True
        if command == "/config":
            parts = text.split(maxsplit=2)
            if len(parts) > 2:
                self._state.log.append(_LogEntry("system", "Usage: /config [setting]"))
                self._refresh()
                return True
            query = parts[1].strip() if len(parts) > 1 else ""
            self._open_config_browser(query)
            return True
        if command in {"/rewind", "/revert"}:
            parts = text.split()
            if len(parts) == 1:
                choices = self._agent_app.service.choices(command[1:])
                if not choices:
                    self._state.log.append(
                        _LogEntry("system", "No user prompts on the active path.")
                    )
                    return True
                callback = (
                    self._open_rewind_actions
                    if command == "/rewind"
                    else self._open_revert_range
                )
                self._open_command_choices(
                    [(label, value) for value, label in choices],
                    lambda selected: callback(str(selected)),
                )
                self._command_choices._selected_index = len(choices) - 1
                self._command_choices.current_value = choices[-1][0]
            elif command == "/rewind" and len(parts) == 2:
                self._open_rewind_actions(parts[1])
            else:
                self._confirm_recovery(text)
            return True
        if command == "/tree" and len(text.split()) == 1:
            service = self._agent_app.service
            choices = service.choices("tree")
            if not choices:
                self._state.log.append(
                    _LogEntry("system", "No conversation history yet.")
                )
                return True
            current = next(
                (node.entry.id for node in service.history() if node.current), None
            )
            self._open_command_choices(
                [(label, name) for name, label in choices],
                lambda selected: self._run_command(f"/tree {selected}"),
                describe=lambda _: (
                    "Stay in this conversation and preserve all branches. "
                    "User inputs reopen for editing; other entries continue after that point. "
                    "Workspace files do not change."
                ),
            )
            for index, (entry_id, _) in enumerate(choices):
                if entry_id == current:
                    self._command_choices._selected_index = index
                    self._command_choices.current_value = entry_id
                    break
            return True
        if command == "/fork" and len(text.split()) == 1:
            entries = self._store.get_forkable_user_messages()
            if not entries:
                self._state.log.append(
                    _LogEntry("system", "No user messages to fork from.")
                )
                self._refresh()
                return True
            self._open_command_choices(
                [
                    (
                        f"{index}. {' '.join(fork_input_text(entry).split())[:100]}",
                        entry.id,
                    )
                    for index, entry in enumerate(entries, 1)
                ],
                lambda selected: self._run_command(f"/fork {selected}"),
                describe=lambda _: (
                    "Copy history before this user turn into a new conversation. "
                    "Edit and resend the input; workspace files stay unchanged."
                ),
            )
            self._command_choices._selected_index = len(entries) - 1
            self._command_choices.current_value = entries[-1].id
            return True
        if command in {"/sessions", "/resume", "/continue"} and len(text.split()) == 1:
            sessions = self._store.list_infos()
            if not sessions:
                self._state.log.append(_LogEntry("system", "No conversations found."))
                self._refresh()
                return True

            def resume(session: object) -> None:
                self._store.resume(getattr(session, "id", ""))
                self._agent_app.restore_session()
                self._restore_session_history()

            self._open_command_choices(
                [(f"{s.title} ({s.id[:8]})", s) for s in sessions],
                resume,
                describe=lambda _: (
                    "Resume a saved conversation. /sessions opens the same picker."
                ),
            )
            return True
        return False

    def _open_rewind_actions(self, user_id: str) -> None:
        """先选恢复范围，不在菜单选择时修改状态。"""
        service = self._agent_app.service
        turn = service.resolve_turn(user_id)
        if turn is None:
            self._state.log.append(_LogEntry("error", "User prompt not found."))
            return
        choices = [("Restore conversation", f"/rewind {turn.id} conversation")]
        description = "Select the state to restore. Workspace restoration covers tracked files only."
        try:
            _, changes = service.recovery.plan(service.log, turn.id, scope="both")
        except (OSError, RuntimeError, ValueError) as exc:
            description += f" Code restore unavailable: {exc}"
        else:
            if changes:
                choices = [
                    ("Restore code and conversation", f"/rewind {turn.id} both"),
                    *choices,
                    ("Restore code", f"/rewind {turn.id} code"),
                ]
        self._open_command_choices(
            choices,
            lambda value: self._confirm_recovery(str(value)),
            describe=lambda _: description,
        )

    def _open_revert_range(self, user_id: str) -> None:
        choices = list(self._agent_app.service.choices("revert"))
        start = next(
            (i for i, (value, _) in enumerate(choices) if value == user_id), None
        )
        if start is None:
            return
        ends = [
            ("Through the latest action", ""),
            *[(f"Through: {label}", value) for value, label in choices[start:]],
        ]
        self._open_command_choices(
            ends, lambda end: self._open_revert_files(user_id, str(end))
        )

    def _open_revert_files(self, user_id: str, end_id: str) -> None:
        import shlex

        command = f"/revert {user_id}" + (f" {end_id}" if end_id else "")
        service = self._agent_app.service
        try:
            _, changes = service.recovery.plan(
                service.log, user_id, scope="code", end_id=end_id or None
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._state.log.append(_LogEntry("error", str(exc)))
            return
        if not changes:
            self._state.log.append(
                _LogEntry("system", "No tracked file changes in this range.")
            )
            return
        choices = [(f"All {len(changes)} tracked files", command)]
        choices.extend(
            (change.path, f"{command} -- {shlex.quote(change.path)}")
            for change in changes
        )
        self._open_command_choices(
            choices,
            lambda value: self._confirm_recovery(str(value)),
            describe=lambda _: (
                "Choose all files or one file. For multiple selected files, use /revert START [END] -- PATH ..."
            ),
        )

    def _confirm_recovery(self, command: str) -> None:
        service = self._agent_app.service
        try:
            user_id, scope, end_id, paths = service.recovery_arguments(command)
            _, changes = service.recovery.plan(
                service.log, user_id, scope=scope, end_id=end_id, paths=paths
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._state.log.append(_LogEntry("error", str(exc)))
            return
        files = ", ".join(change.path for change in changes) or "No file changes"
        description = f"Scope: {scope}. {files}. Only tracked file mutations are restored; untracked shell and external effects remain. Conflicting manual edits block the whole restore."
        self._open_command_choices(
            [("Cancel", "cancel"), ("Apply restore", "apply")],
            lambda value: (
                self._run_command(command, recovery_confirmed=True)
                if value == "apply"
                else None
            ),
            describe=lambda _: description,
        )

    def _open_model_selector(
        self, query: str = "", provider: str | None = None
    ) -> None:
        """在主 TUI 内选择模型，避免嵌套终端事件循环。"""
        from cade.ai.models import parse_model_mode
        from cade.ai.resolver import ModelResolver

        get_model_info = self._agent_app.get_model_info
        info = get_model_info()
        current_model = str(info.get("model", ""))
        current_transport = str(info.get("transport", ""))
        available = [
            entry
            for entry in get_available_model_entries(self._agent_app)
            if (provider is None or entry.provider == provider)
            and query.casefold() in f"{entry.provider}/{entry.model}".casefold()
        ]
        if not available:
            self._state.log.append(
                _LogEntry(
                    "system",
                    "No available models with a signed-in account or configured API key were found.\n"
                    "Hint: run /login to sign in to ChatGPT, or configure an API key in the environment or .env file.",
                )
            )
            self._refresh()
            return

        efforts: dict[tuple[str, str], str | None] = {}

        def entry_title(entry: AvailableModelEntry) -> AnyFormattedText:
            key = (entry.model, entry.transport)
            levels = reasoning_effort_levels_for_transport(entry.transport, entry.model)
            if key not in efforts:
                current_effort = info.get("reasoning_effort")
                efforts[key] = (
                    current_effort
                    if current_effort in levels
                    else next(
                        (level for level in ("medium", "high") if level in levels),
                        levels[0] if levels else None,
                    )
                )
            style = (
                "class:model-current"
                if is_current_model_entry(entry, current_model, current_transport)
                else ""
            )
            fragments = [(style, format_model_entry(entry) + "  ")]
            effort = efforts[key]
            if effort is not None:
                index = levels.index(effort)
                fragments.append(("class:status", "‹ " if index > 0 else "  "))
                fragments.append(("class:status-accent bold", f" {effort} "))
                fragments.append(
                    ("class:status", " ›" if index < len(levels) - 1 else "  ")
                )
            else:
                fragments.append(("class:status", "effort: n/a"))
            return FormattedText(fragments)

        def adjust(selection: object, step: int) -> None:
            if not isinstance(selection, AvailableModelEntry):
                return
            key = (selection.model, selection.transport)
            levels = reasoning_effort_levels_for_transport(
                selection.transport, selection.model
            )
            effort = efforts[key]
            if not levels or effort is None:
                return
            index = max(0, min(levels.index(effort) + step, len(levels) - 1))
            efforts[key] = levels[index]
            self._command_choices.values = [
                (
                    value,
                    entry_title(value)
                    if isinstance(value, AvailableModelEntry) and value is selection
                    else label,
                )
                for value, label in self._command_choices.values
            ]

        def open_selector(initial: AvailableModelEntry | None = None) -> None:
            choices: list[tuple[AnyFormattedText, object]] = []
            for entry in available:
                choices.append((entry_title(entry), entry))
            choices.append(("Enter a custom model name...", "__custom__"))
            self._open_command_choices(choices, choose, on_adjust=adjust)
            for index, entry in enumerate(available):
                if entry is initial or (
                    initial is None
                    and is_current_model_entry(entry, current_model, current_transport)
                ):
                    self._command_choices._selected_index = index
                    self._command_choices.current_value = entry
                    break
            self._refresh()

        def switch(entry: AvailableModelEntry) -> None:
            try:
                effort = efforts[(entry.model, entry.transport)]
                service = self._agent_app.service
                service.select_model(
                    f"{entry.provider}/{entry.model}",
                    effort=effort,
                    thinking=(
                        effort != "off"
                        if effort is not None
                        and entry.transport not in {"openai_responses", "openai_codex"}
                        else None
                    ),
                )
                new_model = (
                    f"{service.provider_config.provider}/{service.provider.model}"
                )
                updated_info = get_model_info()
                transport = str(updated_info.get("transport") or entry.transport)
                suffix = f" (transport: {transport})" if transport else ""
                if effort is not None:
                    suffix += f" · effort: {effort}"
                self._state.log.append(
                    _LogEntry(
                        "system",
                        f"✓ Successfully switched to model: {new_model}{suffix}",
                    )
                )
            except (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                self._state.log.append(
                    _LogEntry("error", f"Failed to switch model: {exc}")
                )

        def add_custom_model(value: str) -> None:
            if not value.strip():
                open_selector()
                return
            try:
                parsed = parse_model_mode(value.strip())
                resolution = ModelResolver.resolve(
                    model_or_alias=parsed.model,
                    provider=parsed.provider,
                    fallback_transport=current_transport,
                )
                entry = next(
                    (
                        item
                        for item in available
                        if (item.model, item.transport)
                        == (resolution.model, resolution.transport)
                    ),
                    None,
                )
                if entry is None:
                    entry = AvailableModelEntry(
                        resolution.model,
                        resolution.transport,
                        resolution.provider,
                        "[custom]",
                    )
                    available.append(entry)
                levels = reasoning_effort_levels_for_transport(
                    entry.transport, entry.model
                )
                if parsed.thinking_level in levels:
                    efforts[(entry.model, entry.transport)] = parsed.thinking_level
                open_selector(entry)
            except ValueError as exc:
                self._state.log.append(_LogEntry("error", str(exc)))
                open_selector()

        def choose(selection: object) -> None:
            if selection == "__custom__":
                self._open_command_text(
                    "Enter the model name (esc to return)",
                    add_custom_model,
                    on_cancel=open_selector,
                )
                return
            entry = cast("AvailableModelEntry", selection)
            switch(entry)

        open_selector()

    def _open_effort_selector(self) -> bool:
        """当前模型支持 effort 时打开选择菜单；不支持时交给命令输出说明。"""
        get_model_info = getattr(self._agent_app, "get_model_info", None)
        raw_info = get_model_info() if callable(get_model_info) else None
        info = cast("dict[str, str]", raw_info) if isinstance(raw_info, dict) else {}
        levels = reasoning_effort_levels_for_transport(
            str(info.get("transport", "")), str(info.get("model", ""))
        )
        if not levels:
            return False
        current = info.get("reasoning_effort")
        self._open_command_choices(
            [
                (f"{level} (current)" if level == current else level, level)
                for level in levels
            ],
            lambda selected: self._run_command(f"/effort {selected}"),
        )
        if current in levels:
            self._command_choices._selected_index = levels.index(current)
            self._command_choices.current_value = current
        return True

    def _post_log(self, kind: str, text: str) -> None:
        """从任意线程追加一条界面消息。"""

        def append() -> None:
            self._state.log.append(_LogEntry(kind, text))
            self._refresh()

        self._call_in_ui_thread(append)

    def _run_login_task(self, work: LoginTask) -> None:
        """在后台线程等待账户授权；运行期间 Esc 设置取消事件。"""
        cancel = Event()
        self._login_cancel = cancel
        self._state.running = True
        self._refresh()

        def finish() -> None:
            self._login_cancel = None
            self._state.running = False
            self._refresh()
            self._submit_pending_input()

        def run() -> None:
            try:
                work(cancel)
            finally:
                self._call_in_ui_thread(finish)

        self._login_thread = threading.Thread(target=run, daemon=True)
        self._login_thread.start()

    def _command_choice_hint_text(self) -> str:
        """选择菜单底部的灰色说明：跟随高亮项，否则显示操作提示。"""
        request = self._state.pending_command_choice
        if request is None:
            return ""
        if request.on_adjust is not None:
            return "\n  ↑/↓ model · ←/→ effort · enter apply · esc cancel"
        if request.describe is not None:
            description = request.describe(self._command_choices.current_value)
            if description:
                return f"\n  {description}"
        return "\n  ↑/↓ move · enter select · esc back/cancel"

    def _open_command_choices(
        self,
        choices: Sequence[tuple[AnyFormattedText, object]],
        on_select: Callable[[object], None],
        on_cancel: Callable[[], None] | None = None,
        describe: Callable[[object], str] | None = None,
        on_adjust: Callable[[object, int], None] | None = None,
    ) -> None:
        """打开可复用的 TUI 命令选择菜单。

        on_cancel 提供时，esc 触发它而不是直接关闭（用于二级菜单返回上级）；
        describe 提供时，底部灰色说明跟随高亮项。
        on_adjust 提供时，左右键调整当前项的附加选项。
        """
        stored_choices = list(choices)
        self._state.pending_command_choice = _CommandChoiceRequest(
            stored_choices, on_select, on_cancel, describe, on_adjust
        )
        self._command_choices.values = [
            (value, label) for label, value in stored_choices
        ]
        self._command_choices._selected_index = 0
        self._command_choices.current_value = stored_choices[0][1]
        self._application.layout.focus(self._command_choices)
        self._refresh()

    def _adjust_command_choice(self, step: int) -> None:
        """左右键调整当前菜单项的附加选项。"""
        request = self._state.pending_command_choice
        if request is not None and request.on_adjust is not None:
            request.on_adjust(self._command_choices.current_value, step)
            self._refresh()

    def _open_command_text(
        self,
        prompt: str,
        on_submit: Callable[[str], None],
        on_cancel: Callable[[], None] | None = None,
        *,
        secret: bool = False,
    ) -> None:
        """打开单行文本表单。"""
        self._state.pending_command_text = _CommandTextRequest(
            prompt, on_submit, on_cancel, secret=secret
        )
        self._application.layout.focus(self._input)
        self._refresh()

    def _submit_command_text(self, text: str) -> None:
        request = self._state.pending_command_text
        if request is None:
            return
        self._state.pending_command_text = None
        request.on_submit(text)
        self._refresh()

    def _open_config_browser(self, query: str = "") -> None:
        """在 TUI 内打开设置浏览器：选择行进入编辑流程。"""
        from .config_registry import (
            SETTING_SPECS,
            find_setting,
            format_setting,
            load_effective_config,
            matching_settings,
        )
        config_path = self._config_path or Path(DEFAULT_CONFIG_NAME)
        if not config_path.is_absolute():
            config_path = self._project_root / config_path
        config = load_effective_config(config_path)

        def choose(selection: object) -> None:
            if selection is None:
                return
            self._edit_config_setting(config_path, cast("SettingSpec", selection))

        if query:
            spec = find_setting(query)
            if spec is None:
                matches = matching_settings(query)
                note = (
                    f"'{query}' is ambiguous: "
                    + ", ".join(match.label for match in matches)
                    if matches
                    else f"No setting matches '{query}'."
                )
                self._state.log.append(_LogEntry("system", note))
                self._refresh()
                return
            choose(spec)
            return

        choices: list[tuple[str, object]] = [
            (f"{item.label:<28}{format_setting(item, config)}", item)
            for item in SETTING_SPECS
        ]
        choices.append(("Exit", None))

        def describe(item: object) -> str:
            if isinstance(item, SettingSpec):
                return f"{item.key} — {item.description}"
            return ""

        self._open_command_choices(choices, choose, describe=describe)

    def _edit_config_setting(self, config_path: Path, spec: SettingSpec) -> None:
        """先选择预设值，仅自定义值使用预填表单。"""
        from .config_registry import (
            MenuItem,
            SettingKind,
            commit_setting_value,
            format_setting,
            load_effective_config,
            save_setting_text,
            setting_detail,
            setting_menu,
        )

        config = load_effective_config(config_path)
        current = format_setting(spec, config)
        current_value = "none" if current == "(unset)" else current

        def reopen() -> None:
            self._open_config_browser()

        def report_and_reopen(ok: bool, message: str) -> None:
            level = "system" if ok else "error"
            self._state.log.append(_LogEntry(level, message))
            reopen()

        if spec.kind is SettingKind.INFO:
            lines = [f"{spec.label} ({current}):"]
            if spec.description:
                lines.append(f"  {spec.description}")
            lines.extend(setting_detail(spec, config))
            for line in lines:
                self._state.log.append(_LogEntry("system", line))
            reopen()
            return

        def ask_text() -> None:
            hint = "Enter save · Esc back"
            if spec.nullable:
                hint += " · none reset"

            def submit(value: str) -> None:
                ok, message = save_setting_text(config_path, spec, value)
                report_and_reopen(ok, message)

            self._open_command_text(
                f"{spec.label} · {hint}",
                submit,
                on_cancel=lambda: self._edit_config_setting(config_path, spec),
            )
            self._input.text = "" if current == "(unset)" else current
            self._input.buffer.cursor_position = len(self._input.text)

        def pick(selection: object) -> None:
            item = cast("MenuItem", selection)
            if item.custom:
                ask_text()
                return
            ok, message = commit_setting_value(config_path, spec, item.value)
            report_and_reopen(ok, message)

        items = setting_menu(spec, config)
        if not items:
            self._state.log.append(
                _LogEntry(
                    "error",
                    f"No options available for {spec.label}; set up credentials first.",
                )
            )
            reopen()
            return

        self._open_command_choices(
            [
                (
                    f"{item.title} (current)"
                    if item.value == current_value and not item.custom
                    else item.title,
                    item,
                )
                for item in items
            ],
            pick,
            on_cancel=reopen,
            describe=lambda item: cast("MenuItem", item).description,
        )
        for index, item in enumerate(items):
            if item.value == current_value and not item.custom:
                self._command_choices._selected_index = index
                self._command_choices.current_value = item
                break

    def _accept_command_choice(self) -> None:
        request = self._state.pending_command_choice
        if request is None:
            return
        selected = self._command_choices.current_value
        self._state.pending_command_choice = None
        # 先恢复输入框焦点，再执行回调：链式打开的下一级菜单或文本表单
        # 会在回调内重新聚焦，不能被这里的默认焦点覆盖。
        self._application.layout.focus(self._input)
        request.on_select(selected)
        self._refresh()

    def _finish_command(
        self,
        text: str,
        should_exit: bool,
        output: str = "",
        error: str | None = None,
        *,
        changed_session: bool = False,
    ) -> None:
        """同步命令执行后的 TUI 状态。"""
        self._state.mode = self._interaction_state.mode
        self._state.running = False
        if error is None and changed_session and _is_session_history_command(text):
            self._restore_session_history()
        if error is not None:
            self._state.log.append(_LogEntry("error", error))
        elif output.strip():
            self._state.log.append(_LogEntry("system", output.rstrip()))
        if self._interaction_state.draft_input is not None:
            self._input.buffer.text = self._interaction_state.draft_input
            self._input.buffer.cursor_position = len(self._input.buffer.text)
            self._interaction_state.draft_input = None
        self._state.running = False
        self._refresh()
        if should_exit:
            self._quit_key(None)
        self._submit_pending_input()

    def _clear_session(self) -> None:
        """创建空会话，由主屏幕渲染器重建欢迎文档。"""
        self._store.clear()
        self._agent_app.restore_session()
        self._state.restore_history([])
        self._state.log.append(_LogEntry("welcome", self._welcome_text()))
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            agent.session_id = self._store.session_id
            self._context_summary = compute_context_summary(agent)
        self._refresh()

    def _run_shell_shortcut(self, text: str) -> None:
        """在后台执行 shell 快捷命令，保持 TUI 事件循环可响应。"""
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            token = getattr(agent, "cancellation_token", None)
            if token is not None:
                token.reset()
        self._state.running = True
        self._refresh()

        def run() -> None:
            try:
                output = run_shell_shortcut(text, self._agent_app)
                self._state.log.append(_LogEntry("shell", output, markdown=False))
            except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
                detail = str(exc) or repr(exc)
                self._state.log.append(
                    _LogEntry("error", f"[error] {type(exc).__name__}: {detail}")
                )
            finally:
                self._state.running = False
                self._call_in_ui_thread(self._schedule_turn_commit)

        self._turn_thread = threading.Thread(target=run, daemon=True)
        self._turn_thread.start()

    def _restore_session_history(self) -> None:
        """恢复会话命令完成后，以 TUI 形式重新渲染当前分支。"""
        self._state.restore_history(self._store.build_branch())
        if hasattr(self, "_input"):
            self._input.text = self._agent_app.service.log.navigation_draft or ""
            self._input.buffer.cursor_position = len(self._input.text)
        agent = getattr(self._agent_app, "agent", None)
        if agent is not None:
            agent.session_id = self._store.session_id
        self._sync_mode_from_agent()

    def _sync_mode_from_agent(self) -> None:
        """从应用权限策略读取模式，同时更新两处显示状态。"""
        agent = getattr(self._agent_app, "agent", None)
        mode = getattr(agent, "current_mode", None)
        if mode in {"plan", "build", "ask"}:
            self._interaction_state.mode = mode
            self._state.mode = mode

    def _restore_startup_session(
        self,
        resume_latest: bool,
        auto_continue: bool,
        session_id: str | None,
    ) -> None:
        """在 TUI 创建前选择并恢复会话，避免以空白会话覆盖历史。"""
        selected = None
        if session_id == self._store.session_id:
            self._restore_session_history()
            return
        if session_id is not None:
            selected = self._store.find_by_id(session_id)
            if selected is None:
                raise ValueError(f"Session not found: {session_id}")
            stored = (
                Path(selected.project_path).resolve() if selected.project_path else None
            )
            if stored is None or stored != self._project_root.resolve():
                raise ValueError(
                    f"Session belongs to another project: {selected.project_path}"
                )
        elif auto_continue:
            selected = self._store.find_latest_for_project(self._project_root)
            if selected is None:
                self._startup_notice = (
                    "No previous task in this project. Start a new task below."
                )
        elif resume_latest:
            selected = select_session_interactively(
                self._store.list_infos(), "Select session to resume:"
            )
        if selected is None:
            return
        self._store.resume(selected.id)
        self._agent_app.restore_session()
        self._state.restore_history(self._store.build_branch())
        self._startup_notice = (
            f"Resumed {selected.title} ({selected.id}). Continue your task below."
        )

    # ── 用户审批 ──

    async def _answer_questions(
        self, questions: Sequence[object]
    ) -> list[list[str]] | None:
        """把 Cade 的 Question 对象转换为 TUI 选择菜单。"""
        payload: list[dict[str, object]] = []
        for question in questions:
            options = getattr(question, "options", ())
            payload.append(
                {
                    "question": getattr(question, "text", ""),
                    "multiple": getattr(question, "multiple", False),
                    "options": [
                        {"label": label, "description": description}
                        for label, description in options
                    ],
                }
            )
        return await asyncio.to_thread(self._question_prompt_callback, payload)

    def _question_prompt_callback(
        self, questions: list[dict[str, object]]
    ) -> list[list[str]] | None:
        """在现有 TUI application 内依次收集 question 工具回答。"""
        answers: list[list[str]] = []
        for question in questions:
            options = question.get("options")
            if isinstance(options, list) and options:
                choices = [
                    (
                        _question_choice_text(option),
                        str(option.get("label", "")),
                    )
                    for option in options
                    if isinstance(option, dict)
                ]
                # 追加「自定义输入」选项
                choices.append((CUSTOM_OPTION_LABEL, CUSTOM_OPTION_LABEL))

                result = self._wait_for_question_choice(
                    str(question.get("question", "")),
                    choices,
                    bool(question.get("multiple", False)),
                )
                if result is None:
                    return None

                multiple = bool(question.get("multiple", False))
                if multiple:
                    if CUSTOM_OPTION_LABEL in result:
                        custom = self._wait_for_question_text(
                            str(question.get("question", ""))
                        )
                        if custom is None:
                            return None
                        answers.append(
                            [
                                *(
                                    value
                                    for value in result
                                    if value != CUSTOM_OPTION_LABEL
                                ),
                                *custom,
                            ]
                        )
                    else:
                        answers.append(result)
                else:
                    if result and result[0] == CUSTOM_OPTION_LABEL:
                        custom = self._wait_for_question_text(
                            str(question.get("question", ""))
                        )
                        if custom is None:
                            return None
                        answers.append(custom if custom else [])
                    else:
                        answers.append(result)
                continue

            custom = self._wait_for_question_text(str(question.get("question", "")))
            if custom is None:
                return None
            answers.append(custom)
        return answers

    def _wait_for_question_text(self, prompt: str) -> list[str] | None:
        """打开文本输入并等待用户提交回答。"""
        event = Event()
        answer: list[str] = []
        submitted = False

        def submit(value: str) -> None:
            nonlocal submitted
            submitted = True
            if value:
                answer.append(value)
            event.set()

        def show_text() -> None:
            self._open_command_text(prompt, submit, event.set)

        self._call_in_ui_thread(show_text)
        event.wait()
        return answer if submitted else None

    def _wait_for_question_choice(
        self,
        prompt: str,
        choices: list[tuple[str, str]],
        multiple: bool,
    ) -> list[str] | None:
        request = _QuestionChoiceRequest(prompt, choices, multiple, Event())

        def show_choices() -> None:
            self._state.pending_question_choice = request
            values = [(value, label) for label, value in choices]
            if multiple:
                self._question_checkboxes.values = values
                self._question_checkboxes._selected_index = 0
                self._question_checkboxes.current_values = []
                self._application.layout.focus(self._question_checkboxes)
            else:
                self._question_choices.values = values
                self._question_choices._selected_index = 0
                self._question_choices.current_value = choices[0][1]
                self._application.layout.focus(self._question_choices)
            self._refresh()

        self._call_in_ui_thread(show_choices)
        request.event.wait()
        return request.result

    def _call_in_ui_thread(self, callback: Callable[[], None]) -> None:
        loop = self._application.loop
        if loop is None:
            callback()
        else:
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    def _accept_question_choice(self) -> None:
        request = self._state.pending_question_choice
        if request is None:
            return
        if request.multiple:
            result = list(self._question_checkboxes.current_values)
        else:
            result = [self._question_choices.current_value]
        self._complete_question_choice(result)

    def _complete_question_choice(self, result: list[str] | None) -> None:
        request = self._state.pending_question_choice
        if request is None:
            return
        request.result = result
        self._state.pending_question_choice = None
        request.event.set()
        self._application.layout.focus(self._input)
        self._refresh()

    def _approval_callback(self, approval: ApprovalRequest) -> HITLResult:
        scope_preview = [
            _strip_rich_markup(line)
            for line in approval_scope_lines(
                approval.tool, approval.action_input, approval.allowed_scopes
            )
        ]
        request = _HitlRequest(
            tool_name=approval.tool.name,
            preview=[
                *(
                    _strip_rich_markup(line)
                    for line in tool_preview_lines(approval.tool, approval.action_input)
                ),
                *scope_preview,
            ],
            event=Event(),
            full_preview=(
                [
                    "Tool: edit",
                    *full_edit_preview_lines(approval.action_input),
                    *scope_preview,
                ]
                if approval.tool.name == "edit"
                else None
            ),
        )
        self._state.pending_hitl = request

        def show_choices() -> None:
            choices = hitl_choices(approval.allowed_scopes)
            self._approval_choices.values = [(choice, choice) for choice in choices]
            self._approval_choices._selected_index = 0
            self._approval_choices.current_value = choices[0]
            self._approval_choices.current_values = [choices[0]]
            self._application.layout.focus(self._approval_choices)
            self._refresh()

        loop = self._application.loop
        if loop is None:
            show_choices()
        else:
            loop.call_soon_threadsafe(show_choices)
        request.event.wait()
        result = request.result or HITLResult("deny", "once")
        if self._state.pending_hitl is request:
            self._state.pending_hitl = None
        return result

    def _submit_pending_input(self) -> None:
        """空闲时消费本进程排队输入；退出期间不再启动新任务。"""
        if (
            self._closing
            or self._state.running
            or not self._agent_app.agent.has_pending_input()
        ):
            return
        self._start_working()
        self._turn_thread = threading.Thread(
            target=self._run_turn,
            args=(None, None),
            daemon=True,
        )
        self._turn_thread.start()

    def _accept_approval_choice(self) -> None:
        choice = self._approval_choices.current_value
        if choice == "Deny":
            self._awaiting_denial_suggestion = True
            self._application.layout.focus(self._input)
            self._refresh()
            return
        result = parse_hitl_choice(choice)
        if result is not None:
            self._complete_approval(result)

    def _finish_denial(self, suggestion: str) -> None:
        self._complete_approval(HITLResult("deny", "once", suggestion=suggestion))

    def _complete_approval(self, result: HITLResult) -> None:
        request = self._state.pending_hitl
        if request is None:
            return
        request.result = result
        self._state.pending_hitl = None
        self._awaiting_denial_suggestion = False
        request.event.set()
        self._application.layout.focus(self._input)
        self._refresh()

    # ── Turn 执行 ──

    def _run_turn(self, text: str | None, display_text: str | None) -> None:
        try:
            self._last_stream_refresh = 0.0
            for event in self._agent_app.ask_stream(
                text,
                mode=self._interaction_state.mode,
                display_question=display_text,
            ):
                self._dispatch_agent_event(event)
        except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._wait_for_agent_events()
            detail = str(exc) or repr(exc)
            self._state.log.append(
                _LogEntry("error", f"[error] {type(exc).__name__}: {detail}")
            )
            self._state.running = False
            self._stop_working()
            self._schedule_turn_commit()
            return

        self._wait_for_agent_events()
        self._stop_working()

        self._refresh()

        self._context_summary = compute_context_summary(self._agent_app.agent)
        self._schedule_turn_commit()

    def _schedule_turn_commit(self) -> None:
        """刷新完成回合的完整文档，保留终端历史和折叠能力。"""
        self._refresh()
        self._submit_pending_input()
        self._refresh_workspace_branch()

    def _refresh_workspace_branch(self) -> None:
        """回合结束后在后台读取分支，避免渲染时阻塞终端。"""

        def update() -> None:
            branch = git_branch_name(self._project_root)

            def apply() -> None:
                self._workspace_branch = branch
                self._refresh()

            self._call_in_ui_thread(apply)

        threading.Thread(target=update, daemon=True).start()

    def _dispatch_agent_event(self, event: AgentHarnessEvent) -> None:
        """将 agent 事件放入队列，由 UI loop 批量更新显示状态。"""
        loop = self._application.loop
        if loop is None:
            self._state.handle_event(event)
            self._refresh_streaming()
            return

        self._agent_event_queue.put(event)
        with self._agent_event_queue_lock:
            if self._agent_event_drain_scheduled:
                return
            self._agent_event_drain_scheduled = True
        loop.call_soon_threadsafe(self._drain_agent_events)

    def _drain_agent_events(self) -> None:
        """在 UI 线程批量处理已排队事件，并最多触发一次重绘。"""
        with self._agent_event_drain_lock:
            while True:
                try:
                    event = self._agent_event_queue.get_nowait()
                except Empty:
                    break
                self._state.handle_event(event)
        self._refresh_streaming()
        with self._agent_event_queue_lock:
            self._agent_event_drain_scheduled = False
            has_pending = not self._agent_event_queue.empty()
            if has_pending:
                self._agent_event_drain_scheduled = True
        if has_pending:
            loop = self._application.loop
            if loop is not None:
                loop.call_soon(self._drain_agent_events)

    def _wait_for_agent_events(self) -> None:
        """等待本回合已产生的事件全部进入状态，避免保存结果时竞态。"""
        loop = self._application.loop
        if loop is None:
            return
        if loop.is_closed() or not loop.is_running():
            self._drain_agent_events()
            return
        completed = Event()

        def finish() -> None:
            self._drain_agent_events()
            completed.set()

        try:
            loop.call_soon_threadsafe(finish)
        except RuntimeError:
            self._drain_agent_events()
            return
        if not completed.wait(self._EVENT_DRAIN_TIMEOUT):
            self._drain_agent_events()

    # ── 刷新 ──

    def _output_width(self) -> int:
        return max(1, self._application.output.get_size().columns)

    def _output_height(self) -> int:
        approval_height = self._approval_panel_height()
        input_visible = (
            self._state.pending_command_choice is None
            and (self._state.pending_question_choice is None)
            and (self._state.pending_hitl is None or self._awaiting_denial_suggestion)
        )
        input_area_height = self._input_height() + 2 if input_visible else 0
        command_height = self._command_panel_height()
        question_height = self._question_panel_height()
        available = max(
            1,
            self._application.output.get_size().rows
            - 1
            - input_area_height
            - 2
            - self._input_hint_height()
            - approval_height
            - command_height
            - question_height
            - self._completion_height(),
        )
        if self._render_mode == "fullscreen":
            return available
        return min(available, self._state.line_count(self._output_width()) + 1)

    def _input_height(self) -> int:
        # 输入内容可能没有换行符，但会因终端宽度产生视觉折行。
        # 按显示宽度计算高度，避免视口只露出光标所在的最后一段。
        columns = max(1, self._application.output.get_size().columns - 1)
        prompt_width = fragment_list_width(self._input_prompt())
        visual_lines = 0
        for index, line in enumerate(self._input.text.split("\n")):
            line_width = get_cwidth(line)
            if index == 0:
                line_width += prompt_width
            quotient, remainder = divmod(line_width, columns)
            visual_lines += max(1, quotient + bool(remainder))
        return min(5, visual_lines)

    def _start_working(self) -> None:
        """启动独立的工作状态动画，不把它当作 reasoning。"""
        self._state.start_working()
        self._schedule_working_refresh()
        self._refresh()

    def _stop_working(self) -> None:
        """停止工作状态动画；reasoning 内容由输出状态单独管理。"""
        self._state.stop_working()
        if self._working_refresh_handle is not None:
            self._working_refresh_handle.cancel()
            self._working_refresh_handle = None
        self._refresh()

    def _schedule_working_refresh(self) -> None:
        loop = self._application.loop
        if (
            not self._state.working
            or loop is None
            or not loop.is_running()
            or self._working_refresh_handle is not None
        ):
            return
        self._working_refresh_handle = loop.call_later(0.1, self._refresh_working)

    def _refresh_working(self) -> None:
        """刷新工作动画并安排下一帧。"""
        self._working_refresh_handle = None
        if not self._state.working:
            return
        self._refresh()
        self._schedule_working_refresh()

    def _refresh(self) -> None:
        loop = self._application.loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._application.invalidate)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    def _prepare_frame(self) -> None:
        """在 UI 线程生成完整文档，屏幕之外的内容由渲染器写入历史。"""
        if self._state.log and self._state.log[0].role == "welcome":
            self._state.log[0].text = self._welcome_text()
        lines = self._state.ansi_lines(self._output_width())
        if self._inline_renderer is not None:
            self._output_control.text = self._inline_renderer.prepare(
                lines, self._output_height()
            )
            if self._application.is_done:
                self._inline_renderer.finish()
        else:
            self._output_control.text = self._fullscreen_transcript.prepare(
                lines, self._output_height()
            )

    def _refresh_streaming(self) -> None:
        """限制流式输出重绘频率，避免每个 delta 都触发完整布局。"""
        now = perf_counter()
        elapsed = now - self._last_stream_refresh
        if elapsed < self._STREAM_REFRESH_INTERVAL:
            loop = self._application.loop
            if (
                loop is not None
                and loop.is_running()
                and self._stream_refresh_handle is None
            ):
                self._stream_refresh_handle = loop.call_later(
                    self._STREAM_REFRESH_INTERVAL - elapsed,
                    self._flush_stream_refresh,
                )
            return
        if self._stream_refresh_handle is not None:
            self._stream_refresh_handle.cancel()
            self._stream_refresh_handle = None
        self._last_stream_refresh = now
        self._refresh()

    def _flush_stream_refresh(self) -> None:
        """补发节流窗口末尾的最后一次流式重绘。"""
        self._stream_refresh_handle = None
        self._last_stream_refresh = perf_counter()
        self._refresh()

    def _status_text(self) -> FormattedText:
        width = self._application.output.get_size().columns
        project = compact_path(self._project_root)
        if self._workspace_branch:
            project += f" ({self._workspace_branch})"
        context = (
            f"context: {self._context_summary.context_usage}"
            if self._context_summary.context_usage
            else ""
        )
        if self._context_summary.usage_stats and width >= 90:
            context += f"  {self._context_summary.usage_stats}"
        first = status_line(
            project,
            context,
            width,
            keep_left_end=True,
            left_style="class:status-accent",
        )
        if self._workspace_branch:
            path, separator, branch = first[0][1].rpartition(" (")
            if separator:
                first[:1] = [
                    ("class:status-accent", path),
                    ("class:status", separator + branch),
                ]
        mode = f"● {self._interaction_state.mode}"
        if self._state.working:
            mode = f"{working_status_text()}  {mode}"
        elif self._state.pending_hitl is not None:
            mode = "Awaiting approval"
        second = status_line(
            mode,
            self._model_status(),
            width,
            left_style="class:status-mode",
            right_style="class:status",
        )
        return FormattedText([*first, ("", "\n"), *second])

    def _model_status(self) -> str:
        get_model_info = getattr(self._agent_app, "get_model_info", None)
        raw_info = get_model_info() if callable(get_model_info) else None
        info = raw_info if isinstance(raw_info, dict) else {}
        model = str(info.get("model") or self._context_summary.model_name or "unknown")
        effort = str(info.get("reasoning_effort") or "")
        return f"{model} · {effort}" if effort else model

    def _welcome_text(self) -> str:
        return welcome_text(
            version=self._welcome_version,
            tools=self._welcome_tools,
            instructions=self._welcome_instructions,
            skills=self._welcome_skills,
            width=self._output_width(),
            compact=self._application.output.get_size().rows < 28,
        )


# ── 模块级工具函数 ──


def _is_live_command(text: str) -> bool:
    """判断命令是否可以在 agent 回合执行期间提交。"""
    return text.split(maxsplit=1)[0] in {
        "/steer",
        "/queue",
        "/goal",
        "/btw",
        "/verbose",
        "/stop",
        "/quit",
        "/exit",
    }


def _tui_history(project_root: Path) -> History | None:
    """为 TUI 输入栏创建持久化历史记录。"""
    try:
        from prompt_toolkit.history import FileHistory

        history_dir = project_root / ".cade"
        history_dir.mkdir(parents=True, exist_ok=True)
        return FileHistory(str(history_dir / "tui_history"))
    except OSError:
        return None


def _strip_rich_markup(text: str) -> str:
    """Rich 标记不能由 prompt_toolkit 解析，转换为保留内容的纯文本。"""
    return re.sub(r"\[/?[^\]]+]", "", text)


def _question_choice_text(option: dict[str, object]) -> str:
    """组合 question 选项标签和说明。"""
    label = str(option.get("label", ""))
    description = option.get("description")
    if isinstance(description, str) and description.strip():
        return f"{label} - {description.strip()}"
    return label


def _is_session_history_command(command: str) -> bool:
    """显式导航后重建转录；窗口切换保留当前终端记录。"""
    return command.split(maxsplit=1)[0] in {
        "/resume",
        "/continue",
        "/sessions",
        "/tree",
        "/clone",
        "/fork",
        "/undo",
        "/redo",
        "/rewind",
        "/revert",
    }
