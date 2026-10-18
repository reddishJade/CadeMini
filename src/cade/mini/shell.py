"""保守的 shell 命令分类。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

type CommandKind = Literal["read_only", "mutating", "unknown"]

_READ_ONLY = frozenset(
    {
        "cat",
        "dir",
        "echo",
        "find",
        "grep",
        "head",
        "ls",
        "pwd",
        "rg",
        "tail",
        "type",
        "wc",
        "where",
        "which",
    }
)
_MUTATING = frozenset(
    {
        "bash",
        "chmod",
        "chown",
        "cmd",
        "cp",
        "git",
        "ln",
        "mkdir",
        "mv",
        "node",
        "npm",
        "pip",
        "pnpm",
        "powershell",
        "pwsh",
        "python",
        "rm",
        "sh",
        "touch",
        "uv",
    }
)
_READ_ONLY_GIT = frozenset(
    {
        "blame",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "rev-parse",
        "show",
        "status",
    }
)
_SHELL_META = (">", ">>", "|", ";", "&&", "||", "`", "$(", "&")


def classify_command(command: str) -> CommandKind:
    """只把明确只读的单条命令判为 read_only。"""
    text = command.strip()
    if not text:
        return "unknown"
    if any(marker in text for marker in _SHELL_META):
        return "unknown"
    tokens = text.split()
    tool = Path(tokens[0].strip("\"'")).name.lower()
    if tool == "git":
        subcommand = tokens[1].lower() if len(tokens) > 1 else ""
        return "read_only" if subcommand in _READ_ONLY_GIT else "mutating"
    if tool == "find" and any(
        token in {"-delete", "-exec", "-execdir", "-ok"} for token in tokens[1:]
    ):
        return "mutating"
    if tool in _READ_ONLY:
        return "read_only"
    if tool in _MUTATING:
        return "mutating"
    return "unknown"


_REDIRECT = re.compile(r"(?:^|\s)(?:>>?|1>)\s*([^\s|;&]+)")
_SIMPLE_MUTATORS = frozenset(
    {
        "rm",
        "del",
        "erase",
        "remove-item",
        "rmdir",
        "mv",
        "move",
        "move-item",
        "cp",
        "copy",
        "copy-item",
        "touch",
        "mkdir",
        "md",
    }
)


def mutation_targets(command: str) -> tuple[str, ...]:
    """保守提取可能被单条 shell 命令改写的路径，用于变更前快照。"""
    text = command.strip()
    if not text:
        return ()
    if any(marker in text for marker in (";", "&&", "||", "|", "`", "$(")):
        return ()
    tokens = text.split()
    tool = Path(tokens[0].strip("\"'")).name.lower()
    targets: list[str] = []
    if tool == "echo" or ">" in text:
        targets.extend(
            match.group(1).strip("\"'") for match in _REDIRECT.finditer(text)
        )
    if tool in _SIMPLE_MUTATORS:
        args = [token.strip("\"'") for token in tokens[1:]]
        positional = [arg for arg in args if arg and not arg.startswith("-")]
        if tool in {"cp", "copy", "copy-item"}:
            if positional:
                targets.append(positional[-1])
        else:
            targets.extend(positional)
    elif tool == "sed":
        args = [token.strip("\"'") for token in tokens[1:]]
        if "-i" in args:
            positional = [arg for arg in args if not arg.startswith("-")]
            if positional:
                targets.append(positional[-1])
    return tuple(dict.fromkeys(target for target in targets if target))
