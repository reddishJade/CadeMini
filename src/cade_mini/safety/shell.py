"""保守的 shell 命令分类。"""

from __future__ import annotations

import os
import shlex
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
_SHELL_META = (">", ">>", "|", ";", "&&", "||", "`", "$(", "&", "\n", "\r", "<(", ">(")


def classify_command(command: str) -> CommandKind:
    """只把明确只读的单条命令判为 read_only。"""
    text = command.strip()
    if not text:
        return "unknown"
    if any(marker in text for marker in _SHELL_META):
        return "unknown"
    try:
        tokens = shlex.split(text)
    except ValueError:
        return "unknown"
    tool = Path(tokens[0]).name.lower()
    if tool == "git":
        if any(
            token.startswith(("--output", "--ext-diff", "--textconv", "--exec"))
            for token in tokens
        ):
            return "mutating"
        subcommand = tokens[1].lower() if len(tokens) > 1 else ""
        return "read_only" if subcommand in _READ_ONLY_GIT else "mutating"
    if tool == "find" and any(
        token
        in {
            "-delete",
            "-exec",
            "-execdir",
            "-ok",
            "-okdir",
            "-fprint",
            "-fprint0",
            "-fprintf",
            "-fls",
        }
        for token in tokens[1:]
    ):
        return "mutating"
    if tool == "rg" and any(
        token == "--pre" or token.startswith("--pre=") for token in tokens
    ):
        return "mutating"
    if tool in _READ_ONLY:
        return "read_only"
    if tool in _MUTATING:
        return "mutating"
    return "unknown"


def risk_reason(command: str) -> str | None:
    """识别需要授权的显著风险；分类不是操作系统安全边界。"""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return "The command cannot be parsed reliably; review it before execution."
    words = [Path(token).name.lower() for token in tokens]
    destructive = {"rm", "rmdir", "del", "erase", "remove-item", "rd"}
    privileged = {
        "sudo",
        "su",
        "chmod",
        "chown",
        "icacls",
        "set-acl",
        "reg",
        "shutdown",
        "reboot",
    }
    if any(word in destructive for word in words):
        return "The command may delete existing files or directories."
    if any(word in privileged for word in words):
        return "The command may change system state, privileges, or security settings."
    if "git" in words and any(
        word in {"push", "reset", "clean", "rebase"} for word in words
    ):
        return "The command may publish changes, discard work, or rewrite Git history."
    if "curl" in words and any(
        token
        in {
            "-d",
            "--data",
            "--data-binary",
            "-F",
            "--form",
            "-T",
            "--upload-file",
            "-X",
            "--request",
        }
        or token.startswith(("--data=", "--data-binary=", "--form=", "--upload-file="))
        for token in tokens
    ):
        return "The command may send data or modify an external service."
    if any(
        word in {"ssh", "scp", "sftp", "rsync", "kubectl", "terraform"}
        for word in words
    ):
        return "The command may affect remote resources or infrastructure."
    return None


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
    if any(marker in text for marker in (";", "&", "|", "`", "$", "\n", "\r", "<")):
        return ()
    lexer = shlex.shlex(text, posix=os.name != "nt", punctuation_chars=">")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return ()
    if os.name == "nt":
        tokens = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
            else token
            for token in tokens
        ]
    if not tokens:
        return ()
    words: list[str] = []
    targets: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {">", ">>"}:
            if index + 1 == len(tokens):
                return ()
            targets.append(tokens[index + 1])
            index += 2
        else:
            words.append(token)
            index += 1
    if not words:
        return ()
    tool = Path(words[0]).name.lower()
    if tool in {"mkdir", "md", "rmdir"}:
        return ()
    args = words[1:]
    positional = [arg for arg in args if arg and not arg.startswith("-")]
    if tool in {"cp", "copy", "copy-item"}:
        if len(positional) != 2 or any(
            arg.lower()
            in {"-path", "-literalpath", "-destination", "-t", "--target-directory"}
            for arg in args
        ):
            return ()
        targets.append(positional[-1])
    elif tool in {"mv", "move", "move-item"}:
        if len(positional) != 2:
            return ()
        targets.extend(positional)
    elif tool in _SIMPLE_MUTATORS:
        targets.extend(positional)
    elif tool == "sed":
        if "-i" not in args or len(positional) != 2:
            return ()
        targets.append(positional[-1])
    elif tool != "echo":
        return ()
    if any(any(char in target for char in "*?{}") for target in targets):
        return ()
    return tuple(dict.fromkeys(target for target in targets if target))
