"""保守的 shell 命令分类。"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Literal

from cade_mini.safety.sensitive import is_sensitive_path

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
_GIT_GLOBAL_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
)
_SENSITIVE_MARKERS = (
    "/.ssh",
    "/.aws",
    "/.gnupg",
    "/.kube",
    "/.docker",
    "/.config/gh",
    "/.config/gcloud",
    "/.config/hub",
    "/.git-credentials",
    "/.netrc",
    "/.npmrc",
    "/.pypirc",
)
_ENV_PATH = re.compile(r"(?:^|/)\.env(?!\.example)")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "cmd", "powershell", "pwsh"})
_GIT_MUTATING = frozenset(
    {
        "add",
        "am",
        "apply",
        "branch",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "config",
        "fetch",
        "gc",
        "init",
        "merge",
        "mv",
        "prune",
        "pull",
        "push",
        "rebase",
        "remote",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "switch",
        "tag",
        "update-ref",
        "worktree",
    }
)


def _git_subcommand(tokens: list[str]) -> str:
    """跳过 git 的全局选项，返回实际子命令。"""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in _GIT_GLOBAL_OPTIONS:
            index += 2
            continue
        if token.startswith(
            ("--git-dir=", "--work-tree=", "--namespace=", "--config-env=")
        ):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token.lower()
    return ""


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
        subcommand = _git_subcommand(tokens)
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


def _shell_path(raw: str, workspace: Path | None) -> Path | None:
    try:
        candidate = Path(raw).expanduser()
    except RuntimeError:
        return None
    if not candidate.is_absolute():
        candidate = (workspace or Path.cwd()) / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _sensitive_command_reason(tokens: list[str], workspace: Path | None) -> str | None:
    """识别命令参数中显式出现的敏感路径。"""
    for token in tokens:
        raw = token.strip("\"'").lstrip("<>")
        if not raw:
            continue
        lowered = raw.lower()
        if any(marker in lowered for marker in _SENSITIVE_MARKERS) or _ENV_PATH.search(
            raw
        ):
            return "The command may access sensitive data."
        if raw.startswith(("--git-dir=", "--work-tree=")):
            raw = raw.split("=", 1)[1]
        elif raw.startswith("-"):
            continue
        path = _shell_path(raw, workspace)
        if path is not None and is_sensitive_path(path):
            return f"The command may access sensitive data: {path}."
    return None


def _nested_shell_reason(tokens: list[str], workspace: Path | None) -> str | None:
    if not tokens or Path(tokens[0]).name.lower() not in _SHELLS:
        return None
    try:
        index = tokens.index("-c")
    except ValueError:
        return None
    if index + 1 >= len(tokens):
        return None
    return risk_reason(tokens[index + 1], workspace)


def sensitive_command_reason(command: str, workspace: Path | None = None) -> str | None:
    """只检查命令是否涉及敏感数据，供 grants 之前的强制审批使用。"""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    return _nested_shell_reason(tokens, workspace) or _sensitive_command_reason(
        tokens, workspace
    )


def _git_paths(tokens: list[str], workspace: Path | None) -> tuple[Path, ...]:
    paths: list[Path] = []
    for index, token in enumerate(tokens):
        raw: str | None = None
        if token in {"-C", "--git-dir", "--work-tree"} and index + 1 < len(tokens):
            raw = tokens[index + 1]
        elif token.startswith(("--git-dir=", "--work-tree=")):
            raw = token.split("=", 1)[1]
        if raw is not None:
            path = _shell_path(raw, workspace)
            if path is not None:
                paths.append(path)
    return tuple(paths)


def _external_git_reason(tokens: list[str], workspace: Path | None) -> str | None:
    if workspace is None or not tokens or Path(tokens[0]).name.lower() != "git":
        return None
    if _git_subcommand(tokens) not in _GIT_MUTATING:
        return None
    root = workspace.resolve()
    for path in _git_paths(tokens, workspace):
        if path != root and not path.is_relative_to(root):
            return f"The command may modify an external Git repository: {path}."
    return None


def risk_reason(
    command: str,
    workspace: Path | None = None,
) -> str | None:
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
    nested = _nested_shell_reason(tokens, workspace)
    if nested:
        return nested
    sensitive = _sensitive_command_reason(tokens, workspace)
    if sensitive:
        return sensitive
    external_git = _external_git_reason(tokens, workspace)
    if external_git:
        return external_git
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
