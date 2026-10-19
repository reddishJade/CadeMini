"""UI-owned transcript projection. No model calls or session mutations."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cade_mini.core.log import SessionLog
from cade_mini.core.loop import (
    LoopEvent,
    ReasoningDeltaEvent,
    RunResult,
    TextDeltaEvent,
    ToolEndEvent,
    ToolStartEvent,
    UsageEvent,
)

from .chrome import welcome_ansi_lines, welcome_text
from .rendering import markdown_ansi_lines, wrap_ansi_lines

type EntryType = Literal[
    "welcome", "user", "assistant", "side", "tool", "thinking", "notice", "error"
]


@dataclass
class TranscriptEntry:
    kind: EntryType
    text: str = ""
    name: str = ""
    call_id: str = ""
    args: str = ""
    result: str = ""
    error: bool = False
    finished: bool = True
    rendered_markdown: tuple[int, str, list[str]] | None = None


def _is_exploration(entry: TranscriptEntry) -> bool:
    """Only successful, completed read-only calls can be coalesced."""
    return (
        entry.kind == "tool"
        and entry.name in {"read", "glob", "grep", "history"}
        and entry.finished
        and not entry.error
    )


class Transcript:
    """Display state can be rebuilt from durable facts after session changes."""

    def __init__(
        self,
        workspace: Path,
        session: str,
        *,
        tools: tuple[str, ...] = (),
        skills: tuple[str, ...] = (),
        instructions: tuple[Path, ...] = (),
    ) -> None:
        self.workspace = workspace
        self.session = session
        self.tools = tools
        self.skills = skills
        self.instructions = instructions
        self.compact_welcome = False
        self.entries: list[TranscriptEntry] = []
        self.tools_expanded = False
        self.thinking_expanded = False
        self.running = False
        self._cache: tuple[int, int, list[str]] | None = None
        self._generation = 0
        self.welcome()

    def _changed(self) -> None:
        self._generation += 1

    def welcome(self) -> None:
        self.entries = [TranscriptEntry("welcome")]
        self._changed()

    def set_compact_welcome(self, compact: bool) -> None:
        if self.compact_welcome != compact:
            self.compact_welcome = compact
            self._changed()

    def restore(self, log: SessionLog) -> None:
        """Rebuild the *visible* timeline from durable facts, including old context.

        The model's projection intentionally drops turns after compaction or
        rollover. Using it here would also erase those turns from the UI.
        """
        self.session = log.session_id
        self.welcome()
        try:
            facts = log.branch()
        except (ValueError, RuntimeError) as exc:
            self.notice(f"Could not restore session: {exc}", error=True)
            return

        by_id: dict[str, TranscriptEntry] = {}
        for fact in facts:
            data = fact.data
            if fact.type == "user":
                text = data.get("text")
                if isinstance(text, str):
                    label = "↳ " if data.get("source") == "runtime" else ""
                    self.entries.append(TranscriptEntry("user", label + text))
            elif fact.type == "assistant":
                blocks = data.get("blocks")
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            self.entries.append(TranscriptEntry("assistant", text))
                    elif block.get("type") == "tool_call":
                        call_id = block.get("id")
                        name = block.get("name")
                        args = block.get("args", {})
                        if not isinstance(call_id, str) or not isinstance(name, str):
                            continue
                        entry = TranscriptEntry(
                            "tool",
                            name=name,
                            call_id=call_id,
                            args=json.dumps(args, ensure_ascii=False, indent=2),
                        )
                        self.entries.append(entry)
                        by_id[call_id] = entry
            elif fact.type == "tool_result":
                call_id = data.get("call_id")
                content = data.get("content")
                if not isinstance(call_id, str) or not isinstance(content, str):
                    continue
                entry = by_id.get(call_id)
                if entry is None:
                    entry = TranscriptEntry("tool", name="tool", call_id=call_id)
                    self.entries.append(entry)
                entry.result = content
                entry.error = data.get("status") != "ok"
            elif fact.type == "compaction":
                self.entries.append(TranscriptEntry("notice", "Context compacted"))
            elif fact.type == "window":
                self.entries.append(TranscriptEntry("notice", "Context rolled over"))
        self._changed()

    def add_user(self, text: str) -> None:
        self.entries.append(TranscriptEntry("user", text))
        self._changed()

    def notice(self, text: str, *, error: bool = False) -> None:
        self.entries.append(TranscriptEntry("error" if error else "notice", text))
        self._changed()

    def begin_run(self) -> None:
        self.running = True
        self._changed()

    def end_run(self, result: RunResult | None = None) -> None:
        self.running = False
        for entry in self.entries:
            if entry.kind in {"assistant", "thinking", "tool"}:
                entry.finished = True
        if result is not None and result.error:
            self.notice(f"{result.termination}: {result.error}", error=True)
        elif result is not None and result.termination != "completed":
            self.notice(f"Run ended: {result.termination}")
        self._changed()

    def handle(self, event: LoopEvent) -> None:
        if isinstance(event, TextDeltaEvent):
            if (
                self.entries
                and self.entries[-1].kind == "assistant"
                and not self.entries[-1].finished
            ):
                self.entries[-1].text += event.text
            else:
                self.entries.append(
                    TranscriptEntry("assistant", event.text, finished=False)
                )
        elif isinstance(event, ToolStartEvent):
            self.entries.append(
                TranscriptEntry(
                    "tool",
                    name=event.name,
                    call_id=event.call_id,
                    args=json.dumps(event.args, ensure_ascii=False, indent=2),
                    finished=False,
                )
            )
        elif isinstance(event, ToolEndEvent):
            for item in reversed(self.entries):
                if item.kind == "tool" and item.call_id == event.call_id:
                    item.result, item.error = event.content, event.is_error
                    item.finished = True
                    break
            else:
                self.entries.append(
                    TranscriptEntry(
                        "tool",
                        name="tool",
                        call_id=event.call_id,
                        result=event.content,
                        error=event.is_error,
                    )
                )
        elif isinstance(event, ReasoningDeltaEvent):
            if self.entries and self.entries[-1].kind == "thinking":
                self.entries[-1].text += event.text
            else:
                self.entries.append(TranscriptEntry("thinking", event.text))
        elif isinstance(event, UsageEvent):
            return
        self._changed()

    def start_side(self, question: str) -> TranscriptEntry:
        self.notice("BTW · " + question)
        entry = TranscriptEntry("side", finished=False)
        self.entries.append(entry)
        self._changed()
        return entry

    def side_delta(self, entry: TranscriptEntry, chunk: str) -> None:
        entry.text += chunk
        self._changed()

    def finish_side(self, entry: TranscriptEntry) -> None:
        entry.finished = True
        self._changed()

    def toggle_tools(self) -> None:
        self.tools_expanded = not self.tools_expanded
        self._changed()

    def toggle_thinking(self) -> None:
        self.thinking_expanded = not self.thinking_expanded
        self._changed()

    def ansi_lines(self, width: int) -> list[str]:
        width = max(20, width)
        if self._cache is not None and self._cache[:2] == (self._generation, width):
            return self._cache[2]
        result: list[str] = []
        for index, entry in enumerate(self.entries):
            if not self.tools_expanded and _is_exploration(entry):
                if index and _is_exploration(self.entries[index - 1]):
                    continue
                last = index + 1
                while (
                    last < len(self.entries)
                    and _is_exploration(self.entries[last])
                ):
                    last += 1
                if last - index > 1:
                    counts: dict[str, int] = {}
                    for item in self.entries[index:last]:
                        counts[item.name] = counts.get(item.name, 0) + 1
                    detail = ", ".join(
                        f"{name} ×{count}" for name, count in counts.items()
                    )
                    result.append(
                        f"• Explored {last - index} · {detail} "
                        "(Ctrl+O to expand)"
                    )
                    result.append("")
                    continue
            if entry.kind == "welcome":
                result.extend(
                    welcome_ansi_lines(
                        welcome_text(
                            tools=self.tools,
                            instructions=self.instructions,
                            skills=self.skills,
                            width=width,
                            compact=self.compact_welcome,
                        )
                    )
                )
            elif entry.kind == "user":
                result.extend("› " + line for line in entry.text.splitlines() or [""])
            elif entry.kind in {"assistant", "side"}:
                result.append("")
                # Immutable completed turns keep their width-specific rendering;
                # only the active answer is formatted again during streaming.
                cached = entry.rendered_markdown
                if (
                    cached is not None
                    and cached[0] == width
                    and cached[1] == entry.text
                ):
                    rendered = cached[2]
                else:
                    rendered = markdown_ansi_lines(entry.text, width - 2) or [""]
                    if entry.finished:
                        entry.rendered_markdown = (width, entry.text, rendered)
                result.extend(rendered)
            elif entry.kind == "tool":
                preview = self._tool_brief(entry)
                status = "✗" if entry.error else ("✓" if entry.finished else "●")
                result.append(f"{status} {entry.name} {preview}")
                if self.tools_expanded:
                    result.extend(self._tool_details(entry))
                elif entry.error and entry.result:
                    result.append("  " + entry.result.splitlines()[0][:220])
            elif entry.kind == "thinking":
                if entry.text:
                    result.append(
                        "Thinking"
                        + ("" if self.thinking_expanded else "  (Ctrl+T to expand)")
                    )
                    if self.thinking_expanded:
                        result.extend("  " + line for line in entry.text.splitlines())
            elif entry.kind == "error":
                result.append("✗ " + entry.text)
            else:
                result.append("• " + entry.text)
            result.append("")
        output = wrap_ansi_lines(result, width)
        self._cache = (self._generation, width, output)
        return output

    def _tool_details(self, entry: TranscriptEntry) -> list[str]:
        """Full tool arguments/results with readable, untruncated edit diffs."""
        lines: list[str] = []
        try:
            args = json.loads(entry.args) if entry.args else {}
        except json.JSONDecodeError:
            args = {}
        if isinstance(args, dict):
            edits = args.get("edits")
            changes = edits if isinstance(edits, list) else [args]
            diffs: list[str] = []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                old = change.get("old_text")
                new = change.get("new_text")
                if not isinstance(old, str) or not isinstance(new, str):
                    continue
                path = str(change.get("path", args.get("path", "file")))
                diff = difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
                for line in diff:
                    text = line.rstrip("\n")
                    color = (
                        "\x1b[31m"
                        if text.startswith("-") and not text.startswith("---")
                        else "\x1b[32m"
                        if text.startswith("+") and not text.startswith("+++")
                        else ""
                    )
                    diffs.append("  " + color + text + ("\x1b[0m" if color else ""))
            if diffs:
                lines.append("  Changes:")
                lines.extend(diffs)
                # Old/new text is present in the complete diff. Avoid printing
                # it a second time inside a large JSON argument object.
                other = {
                    key: value
                    for key, value in args.items()
                    if key not in {"old_text", "new_text", "edits"}
                }
                if other:
                    lines.append("  Parameters:")
                    lines.extend(
                        "  " + line
                        for line in json.dumps(
                            other, ensure_ascii=False, indent=2
                        ).splitlines()
                    )
            elif entry.args:
                lines.append("  Arguments:")
                lines.extend("  " + line for line in entry.args.splitlines())
        elif entry.args:
            lines.append("  Arguments:")
            lines.extend("  " + line for line in entry.args.splitlines())
        if entry.result:
            lines.append("  Result:")
            lines.extend("  " + line for line in entry.result.splitlines())
        return lines

    def _tool_brief(self, entry: TranscriptEntry) -> str:
        try:
            args = json.loads(entry.args)
        except (TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(args, dict):
            return ""
        if isinstance(args.get("edits"), list):
            return f"· {len(args['edits'])} edits"
        path = args.get("path") or args.get("file_path")
        if entry.name == "write" and isinstance(args.get("content"), str):
            count = len(args["content"].splitlines())
            return f"· {path or 'file'} · {count} lines"
        if entry.name == "edit" and all(
            isinstance(args.get(key), str)
            for key in ("old_text", "new_text")
        ):
            old_count = len(args["old_text"].splitlines())
            new_count = len(args["new_text"].splitlines())
            return f"· {path or 'file'} · {old_count} → {new_count} lines"
        for key in ("command", "path", "file_path", "query", "pattern", "task"):
            if isinstance(args.get(key), str):
                value = args[key].replace("\n", " ")
                return "· " + value[:120] + ("…" if len(value) > 120 else "")
        return ""

    def line_count(self, width: int) -> int:
        return len(self.ansi_lines(width))
