"""终端帮助、选择器和补全共用的命令目录。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    group: str
    args: str = ""
    canonical: str | None = None


SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("/help", "Show commands and keyboard shortcuts", "Help"),
    CommandSpec("/new", "Start a new conversation", "Sessions"),
    CommandSpec(
        "/clear",
        "Alias for /new; keep saved conversations",
        "Sessions",
        canonical="/new",
    ),
    CommandSpec(
        "/continue", "Choose a saved conversation", "Sessions", canonical="/sessions"
    ),
    CommandSpec(
        "/resume",
        "Resume a saved conversation with messages",
        "Sessions",
        "[session ID]",
    ),
    CommandSpec("/sessions", "Same picker as /resume", "Sessions"),
    CommandSpec(
        "/fork",
        "Copy history before a user turn; edit that input",
        "History",
        "[turn|entry ID]",
    ),
    CommandSpec("/clone", "Copy the active path into a new conversation", "History"),
    CommandSpec(
        "/tree",
        "Navigate branches in the current conversation",
        "History",
        "[entry ID]",
    ),
    CommandSpec("/compact", "Summarize old context", "History", "[instructions]"),
    CommandSpec("/rollover", "Start a fresh context window", "History", "[--force]"),
    CommandSpec(
        "/new-context",
        "Start a fresh context window",
        "History",
        "[--force]",
        "/rollover",
    ),
    CommandSpec("/model", "Choose provider and model", "Model", "[provider/]model"),
    CommandSpec("/effort", "Select reasoning effort", "Model", "[level]"),
    CommandSpec("/thinking", "Select reasoning level", "Model", "[level]"),
    CommandSpec(
        "/context-window", "Set maximum context size", "Model", "[size|default]"
    ),
    CommandSpec("/context", "Show context and usage", "Model"),
    CommandSpec("/mode", "Select execution mode", "Execution", "[ask|build|plan]"),
    CommandSpec(
        "/queue",
        "Queue follow-up or change busy input behavior",
        "Execution",
        "[followup|steer|interrupt|task]",
    ),
    CommandSpec(
        "/steer", "Guide the active task at the next step", "Execution", "<message>"
    ),
    CommandSpec("/stop", "Stop the active run", "Execution"),
    CommandSpec("/btw", "Ask an independent side question", "Execution", "<question>"),
    CommandSpec("/goal", "Manage workspace goal", "Execution", "[text|done|clear]"),
    CommandSpec("/login", "Connect a provider account", "Authentication", "[provider]"),
    CommandSpec(
        "/connect",
        "Connect a provider account",
        "Authentication",
        "[provider]",
    ),
    CommandSpec(
        "/logout", "Disconnect a provider account", "Authentication", "[provider]"
    ),
    CommandSpec(
        "/auth",
        "Check or manage authentication",
        "Authentication",
        "[status|login|logout]",
    ),
    CommandSpec(
        "/config", "Browse or change project settings", "Settings", "[setting[=value]]"
    ),
    CommandSpec(
        "/permissions", "Review session permissions", "Settings", "[list|clear]"
    ),
    CommandSpec("/hooks", "Show registered hooks", "Settings"),
    CommandSpec("/tool", "List available tools", "Settings", "[list]"),
    CommandSpec("/skill", "Activate a discovered skill", "Settings", "[name]"),
    CommandSpec("/memory", "Show workspace memory path", "Settings"),
    CommandSpec("/exit", "Exit the terminal", "Exit"),
    CommandSpec("/quit", "Exit the terminal", "Exit", canonical="/exit"),
)

COMMAND_INDEX: dict[str, CommandSpec] = {item.name: item for item in SPECS}
COMMANDS: dict[str, str] = {
    item.name: item.description for item in SPECS if item.canonical is None
}


def command_help() -> tuple[str, ...]:
    result: list[str] = []
    group = ""
    for item in SPECS:
        if item.group != group:
            group = item.group
            result.append(f"{group}")
        label = item.name + (" " + item.args if item.args else "")
        result.append(f"  {label:<28} {item.description}")
    return tuple(result)
