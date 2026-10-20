"""Project-aware completions for commands, models, skills, shell and file references."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from cade_mini.hosts.command_catalog import COMMAND_INDEX, COMMANDS
from cade_mini.hosts.interaction import InteractionService

_BLOCKED = frozenset(
    {".git", ".cade-mini", ".venv", "node_modules", "__pycache__", "dist", "build"}
)
_CACHE_SECONDS = 2.0


def score(query: str, candidate: str) -> tuple[int, int, int] | None:
    needle = query.casefold()
    text = candidate.casefold()
    if text.startswith(needle):
        return (0, 0, len(text))
    at = text.find(needle)
    if at >= 0:
        return (1, at, len(text))
    offset = 0
    indices: list[int] = []
    for char in needle:
        at = text.find(char, offset)
        if at < 0:
            return None
        indices.append(at)
        offset = at + 1
    return (2, (indices[-1] - indices[0]) if indices else 0, len(text))


class CommandArgsSuggester(AutoSuggest):
    """Show the same inline slash-command argument hints as Cade."""

    def get_suggestion(self, buffer: Buffer, document: Document) -> Suggestion | None:
        command = document.text.strip()
        spec = COMMAND_INDEX.get(command)
        if spec is None or not spec.args:
            return None
        return Suggestion(" " + spec.args)


class CadeCompleter(Completer):
    def __init__(self, service: InteractionService) -> None:
        self.service = service
        self._cache: tuple[float, tuple[str, ...]] | None = None

    def _files(self) -> tuple[str, ...]:
        now = time.monotonic()
        if self._cache and now - self._cache[0] < _CACHE_SECONDS:
            return self._cache[1]
        root = self.service.workspace
        try:
            indexed = subprocess.run(
                [
                    "git", "-C", str(root), "ls-files",
                    "-z", "--cached", "--others", "--exclude-standard",
                ],
                capture_output=True,
                check=False,
                timeout=1.5,
            )
        except (OSError, subprocess.TimeoutExpired):
            indexed = None
        if indexed is not None and indexed.returncode == 0:
            files = (
                path.decode("utf-8", errors="surrogateescape")
                for path in indexed.stdout.split(b"\x00")
                if path
            )
            visible = tuple(
                path
                for path in files
                if not self._blocked_path(path)
                and not (root / path).is_symlink()
            )
            self._cache = (now, visible[:15_000])
            return self._cache[1]

        files: list[str] = []
        for directory, folders, names in os.walk(root, followlinks=False):
            folders[:] = [
                name
                for name in folders
                if name not in _BLOCKED
                and not name.startswith(".env")
                and not (Path(directory) / name).is_symlink()
            ]
            for name in names:
                path = Path(directory) / name
                if path.is_symlink() or name.startswith(".env"):
                    continue
                files.append(path.relative_to(root).as_posix())
                if len(files) >= 15_000:
                    break
            if len(files) >= 15_000:
                break
        self._cache = (now, tuple(files))
        return self._cache[1]

    @staticmethod
    def _blocked_path(path: str) -> bool:
        return any(
            part in _BLOCKED or part.startswith(".env")
            for part in Path(path).parts
        )

    def _matches(
        self,
        fragment: str,
        choices: tuple[tuple[str, str], ...],
        start: int,
        limit: int = 30,
    ) -> Iterator[Completion]:
        ranked: list[tuple[tuple[int, int, int], str, str]] = []
        for name, description in choices:
            rank = score(fragment, name)
            if rank is not None:
                ranked.append((rank, name, description))
        ranked.sort(key=lambda item: item[0])
        for _, name, description in ranked[:limit]:
            # 左列已经显示候选名称，说明列只保留额外信息。
            if description.startswith(name):
                suffix = description[len(name) :]
                if not suffix or suffix[0].isspace() or suffix[0] in "([·–—":
                    description = suffix.strip()
            yield Completion(name, start_position=start, display_meta=description)

    def get_completions(
        self,
        document: Document,
        complete_event: object,
    ) -> Iterator[Completion]:
        text = document.text_before_cursor
        stripped = text.lstrip()
        if not stripped:
            return
        if stripped.startswith("/"):
            name, space, argument = stripped.partition(" ")
            if space:
                kind = {
                    "/model": "model",
                    "/mode": "mode",
                    "/effort": "effort",
                    "/resume": "sessions",
                    "/sessions": "sessions",
                    "/fork": "fork",
                    "/tree": "tree",
                    "/thinking": "thinking",
                    "/auth": "auth",
                    "/login": "login",
                    "/connect": "login",
                    "/logout": "logout",
                    "/config": "config",
                    "/context-window": "context-window",
                    "/permissions": "permissions",
                    "/queue": "queue",
                    "/goal": "goal",
                    "/skill": "skill",
                    "/tool": "tool",
                }.get(name)
                if kind is not None:
                    choices = self.service.choices(kind)
                    if kind == "model":
                        choices = tuple(
                            (
                                model,
                                "ChatGPT sign-in"
                                if model.startswith("chatgpt/")
                                else "OpenAI API key"
                                if model.startswith("openai/")
                                else description,
                            )
                            for model, description in choices
                        )
                    yield from self._matches(argument, choices, -len(argument))
                return
            yield from self._matches(
                stripped,
                tuple((name, detail) for name, detail in COMMANDS.items()),
                -len(stripped),
            )
            return
        marker = re.search(r"(?:^|\s)([@$])([^\s]*)$", text)
        if marker is not None:
            token = marker.group(0).lstrip()
            prefix, partial = marker.group(1), marker.group(2)
            if prefix == "$":
                choices = tuple(
                    ("$" + name, "skill") for name, _ in self.service.choices("skill")
                )
            else:
                choices = tuple(("@" + name, "file") for name in self._files())
            yield from self._matches(prefix + partial, choices, -len(token), 60)
            return
        if stripped.startswith("!"):
            token = stripped.split()[-1]
            if "/" in token or len(stripped.split()) > 1:
                choices = tuple((name, "file") for name in self._files())
                yield from self._matches(token, choices, -len(token), 40)
