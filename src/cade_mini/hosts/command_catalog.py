"""Slash-command catalog shared by the terminal help, picker and completer.

Descriptions and argument hints use Cade's interaction vocabulary. Execution
is owned by InteractionService; aliases are kept out of the ordinary help view.
"""

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
    CommandSpec("/clear", "Start a new conversation", "Sessions", canonical="/new"),
    CommandSpec("/continue", "Resume the latest conversation", "Sessions"),
    CommandSpec(
        "/resume", "Choose or resume a conversation", "Sessions", "[session ID]"
    ),
    CommandSpec("/sessions", "Browse project conversations", "Sessions"),
    CommandSpec("/rename", "Rename the current conversation", "Sessions", "<title>"),
    CommandSpec(
        "/fork", "Fork before a user input and edit it", "History", "[turn|entry ID]"
    ),
    CommandSpec("/clone", "Clone current conversation", "History"),
    CommandSpec("/tree", "Browse conversation event history", "History", "[entry ID]"),
    CommandSpec(
        "/rewind", "Remove recent user turns in a new branch", "History", "[N]"
    ),
    CommandSpec("/undo", "Restore file snapshots", "History", "[N|--list]"),
    CommandSpec("/revert", "Restore file snapshots", "History", "[N|--list]", "/undo"),
    CommandSpec("/compact", "Summarize old context", "History"),
    CommandSpec("/rollover", "Start a fresh context window", "History", "[note]"),
    CommandSpec(
        "/new-context", "Start a fresh context window", "History", "[note]", "/rollover"
    ),
    CommandSpec("/model", "Choose provider and model", "Model", "[provider/]model"),
    CommandSpec("/effort", "Select reasoning effort", "Model", "[level]"),
    CommandSpec("/thinking", "Toggle reasoning summaries", "Model", "[on|off]"),
    CommandSpec(
        "/context-window", "Set maximum context size", "Model", "[size|default]"
    ),
    CommandSpec("/context", "Show context and usage", "Model"),
    CommandSpec("/mode", "Select execution mode", "Execution", "[act|build|plan]"),
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
    CommandSpec(
        "/goal", "Manage workspace goal", "Execution", "[text|pause|resume|done|clear]"
    ),
    CommandSpec("/login", "Connect a provider account", "Authentication", "[provider]"),
    CommandSpec(
        "/connect",
        "Connect a provider account",
        "Authentication",
        "[provider]",
        "/login",
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
    CommandSpec("/mcp", "List connected MCP tools", "Settings", "[status]"),
    CommandSpec("/tool", "Run or list tools", "Settings", "[list|NAME JSON]"),
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
        if item.canonical is not None:
            continue
        if item.group != group:
            group = item.group
            result.append(f"{group}")
        label = item.name + (" " + item.args if item.args else "")
        result.append(f"  {label:<28} {item.description}")
    return tuple(result)
