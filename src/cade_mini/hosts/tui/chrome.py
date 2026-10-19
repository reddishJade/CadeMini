"""Terminal appearance and adaptive layout shared by Cade Mini's TUI."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.utils import get_cwidth

TUI_STYLES: dict[str, str] = {
    "": "#c9d1d9",
    "welcome": "#58a6ff bold",
    "welcome-hint": "#8b949e",
    "resource-heading": "#d29922",
    "user": "bg:#21262d #c9d1d9",
    "command": "#58a6ff",
    "thinking": "#8b949e italic",
    "tool": "#8b949e",
    "tool-title": "#8b949e",
    "error": "#ff7b72",
    "border": "#484f58",
    "completion-menu": "bg:#161b22 #8b949e",
    "completion-menu.completion": "bg:#161b22 #c9d1d9",
    "completion-menu.completion.current": "bg:#21262d #58a6ff bold",
    "completion-menu.meta.completion": "bg:#161b22 #8b949e",
    "completion-menu.meta.completion.current": "bg:#21262d #c9d1d9",
    "radio-selected": "#58a6ff bold",
    "model-current": "#58a6ff bold",
    "choice-desc": "#8b949e",
    "status": "#8b949e",
    "status-accent": "#58a6ff",
    "status-mode": "#bc8cff",
    "input": "bg:#21262d #c9d1d9",
    "prompt-marker": "#58a6ff bold",
    "hint": "#8b949e",
    "picker": "bg:#161b22 #c9d1d9",
    "accent": "#58a6ff bold",
    "scrollbar.background": "",
    "scrollbar.button": "",
    "auto-suggestion": "#6e7681",
}

CADE_MINI_LOGO = (
    "█▀▀▀ █▀▀█ █▀▀▄ █▀▀▀",
    "█    █▄▄█ █  █ █▀▀ ",
    "█▄▄▄ █  █ █▄▄▀ █▄▄▄",
)


def current_version() -> str:
    try:
        return version("cade-mini")
    except PackageNotFoundError:
        return "dev"


def compact_path(path: Path) -> str:
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def fit_text(text: str, width: int, *, keep_end: bool = False) -> str:
    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    chars = reversed(text) if keep_end else iter(text)
    kept: list[str] = []
    used = 1
    for char in chars:
        width_of_char = get_cwidth(char)
        if used + width_of_char > width:
            break
        kept.append(char)
        used += width_of_char
    return ("…" + "".join(reversed(kept))) if keep_end else ("".join(kept) + "…")


def status_line(
    left: str,
    right: str,
    width: int,
    *,
    left_style: str = "class:status",
    right_style: str = "class:status",
    keep_left_end: bool = False,
) -> StyleAndTextTuples:
    width = max(1, width)
    right = fit_text(right, max(0, width - min(12, width // 3) - 2))
    left_width = width - get_cwidth(right) - (2 if right else 0)
    left = fit_text(left, left_width, keep_end=keep_left_end)
    result: StyleAndTextTuples = [(left_style, left)]
    if right:
        padding = width - fragment_list_width(result) - get_cwidth(right)
        result.extend([("", " " * max(0, padding)), (right_style, right)])
    return result


def welcome_text(
    *,
    tools: tuple[str, ...],
    instructions: tuple[Path, ...],
    skills: tuple[str, ...],
    width: int,
    compact: bool = False,
) -> str:
    version_text = current_version()
    if compact:
        lines = [f"cade mini v{version_text}", "/ commands · @ files · Ctrl+J newline"]
    else:
        lines = [*CADE_MINI_LOGO]
        lines[-1] += f"  mini v{version_text}"
        lines.extend(
            [
                "",
                "Describe a task to get started.",
                "/ commands · @ files · ! shell · $ skills · ↑/↓ history",
                "Enter send / queue · Alt+Enter steer · Ctrl+J newline",
                "Ctrl+T thinking · Ctrl+O tool details · ? help",
                "cade-mini --continue · /resume choose history",
            ]
        )
    resources = [
        ("Tools", ", ".join(tools)),
        ("Context", ", ".join(compact_path(path) for path in instructions)),
        ("Skills", ", ".join(skills)),
    ]
    for name, content in resources:
        if not content:
            continue
        if compact:
            lines.append(f"[{name}] {fit_text(content, width - len(name) - 4)}")
        else:
            lines.extend(
                [
                    "",
                    f"[{name}]",
                    "  " + fit_text(content, width - 2, keep_end=name == "Context"),
                ]
            )
    return "\n".join(fit_text(line, width) for line in lines)


def welcome_ansi_lines(content: str) -> list[str]:
    lines: list[str] = []
    for line in content.splitlines():
        if line.startswith(("█", "cade mini v")):
            logo, separator, version_text = line.partition("  mini v")
            colored = f"\x1b[38;2;88;166;255;1m{logo}"
            if separator:
                colored += f"\x1b[0;38;2;139;148;158m  mini v{version_text}"
        elif line.startswith("["):
            heading, separator, rest = line.partition("]")
            colored = (
                f"\x1b[38;2;210;153;34m{heading}{separator}\x1b[38;2;139;148;158m{rest}"
            )
        else:
            colored = f"\x1b[38;2;139;148;158m{line}"
        lines.append(colored + "\x1b[0m")
    return lines
