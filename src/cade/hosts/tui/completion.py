"""交互命令、工具名和 @file 引用补全。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText

MAX_FILE_COMPLETIONS = 100
MAX_NAME_COMPLETIONS = 25
BLOCKED_PARTS = {".git", ".venv", "__pycache__", "node_modules"}


@dataclass(frozen=True)
class CompletionItem:
    text: str
    start_position: int
    display_meta: str = ""


class CompletionEngine:
    def __init__(
        self,
        project_root: Path,
        registry: Iterable[Any] = (),
        command_names: Iterable[str] = (),
        command_registry: Mapping[str, Any] | None = None,
        effort_options: Iterable[str] | Callable[[], Iterable[str]] = (),
        model_options: Iterable[str] | Callable[[], Iterable[str]] = (),
        skill_options: Iterable[str] | Callable[[], Iterable[str]] = (),
    ) -> None:
        self.project_root = project_root.resolve()
        self.tool_names = tuple(sorted(tool.name for tool in registry))
        self.command_names = tuple(command_names)
        self._command_meta: dict[str, str] = {}
        self._command_args: dict[str, str] = {}
        if command_registry:
            for name, entry in command_registry.items():
                self._command_meta[name] = getattr(entry, "desc", "")
                args = getattr(entry, "args_desc", "")
                if args:
                    self._command_args[name] = args
        self._effort_options = effort_options
        self._model_options = model_options
        self._skill_options = skill_options

    @property
    def command_args(self) -> dict[str, str]:
        return self._command_args

    def complete(self, text_before_cursor: str) -> list[CompletionItem]:
        if text_before_cursor.startswith("/mode "):
            partial = text_before_cursor[len("/mode ") :]
            return [
                CompletionItem(mode, -len(partial), "execution mode")
                for mode in ("ask", "build", "plan")
                if mode.startswith(partial)
            ]
        if text_before_cursor.startswith("/effort "):
            return self._complete_values(
                text_before_cursor, "/effort ", self._values(self._effort_options)
            )
        if text_before_cursor.startswith("/model "):
            return self._complete_values(
                text_before_cursor, "/model ", self._values(self._model_options)
            )
        if text_before_cursor.startswith("/tool "):
            partial = text_before_cursor[len("/tool ") :]
            if " " not in partial:
                return self._name_matches(self.tool_names, partial, "tool")
        if text_before_cursor.startswith("$"):
            return self._name_matches(
                self._values(self._skill_options), text_before_cursor[1:], "skill", 1
            )
        if text_before_cursor.startswith("@"):
            return self._complete_files(text_before_cursor)
        if text_before_cursor.startswith("/"):
            return self._complete_command(text_before_cursor)
        return []

    def _complete_command(self, text: str) -> list[CompletionItem]:
        if " " in text:
            return []
        return [
            CompletionItem(name, -len(text), self._command_meta.get(name, ""))
            for name in self.command_names
            if name.startswith(text)
        ][:MAX_NAME_COMPLETIONS]

    def _complete_values(
        self, text: str, prefix: str, values: Iterable[str]
    ) -> list[CompletionItem]:
        partial = text[len(prefix) :]
        return [
            CompletionItem(value, -len(partial), "")
            for value in values
            if value.startswith(partial)
        ][:MAX_NAME_COMPLETIONS]

    def _name_matches(
        self, names: Iterable[str], partial: str, meta: str, offset: int = 0
    ) -> list[CompletionItem]:
        return [
            CompletionItem(name, -(len(partial) + offset), meta)
            for name in names
            if name.startswith(partial)
        ][:MAX_NAME_COMPLETIONS]

    def _values(
        self, value: Iterable[str] | Callable[[], Iterable[str]]
    ) -> tuple[str, ...]:
        if callable(value):
            return tuple(value())
        return tuple(value)

    def _complete_files(self, text: str) -> list[CompletionItem]:
        partial = text[1:]
        directory, _, fragment = partial.rpartition("/")
        base = self.project_root / directory if directory else self.project_root
        if not base.is_dir():
            return []
        items: list[CompletionItem] = []
        for child in sorted(base.iterdir(), key=lambda item: item.name.casefold()):
            if child.name.startswith(".") or child.name in BLOCKED_PARTS:
                continue
            if child.is_symlink():
                continue
            relative = f"{directory}/{child.name}" if directory else child.name
            if not child.name.lower().startswith(fragment.lower()):
                continue
            suffix = "/" if child.is_dir() else ""
            items.append(
                CompletionItem(
                    relative + suffix,
                    -len(partial),
                    "dir" if child.is_dir() else "file",
                )
            )
            if len(items) >= MAX_FILE_COMPLETIONS:
                break
        return items


class TuiCompleter(Completer):
    """prompt_toolkit 适配器；真正的候选由 CompletionEngine 生成。"""

    def __init__(self, engine: CompletionEngine) -> None:
        self.engine = engine

    @property
    def command_args(self) -> dict[str, str]:
        return self.engine.command_args

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        del complete_event
        for item in self.engine.complete(document.text_before_cursor):
            yield Completion(
                item.text,
                start_position=item.start_position,
                display_meta=FormattedText([("fg:ansibrightblack", item.display_meta)])
                if item.display_meta
                else None,
            )


class CommandArgsSuggester(AutoSuggest):
    """在命令后显示参数提示。"""

    def __init__(self, command_args: dict[str, str]) -> None:
        self.command_args = command_args

    def get_suggestion(self, buffer: Buffer, document: Document) -> Suggestion | None:
        del buffer
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return None
        args = self.command_args.get(text)
        if args:
            return Suggestion(" " + args)
        return None


__all__ = [
    "CommandArgsSuggester",
    "CompletionEngine",
    "CompletionItem",
    "TuiCompleter",
]
