"""用 fd 和 rg 搜索文件，排除 Cade 内部状态。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    _is_blocked,
    _resolve_authorized_path,
    number_arg,
    text_arg,
)
from cade.coding.tools.native import find_tool
from cade.coding.tools.truncate import MAX_BYTES, format_size, truncate
from cade.harness.execution.local.subprocess import SubprocessShell
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


def _native(
    tool: str, args: list[str], cwd: Path, executable: str | None
) -> tuple[str, int | None]:
    executable = executable or find_tool(tool)
    result = SubprocessShell().run([executable, *args], cwd=cwd, timeout=None)
    if result.cancelled:
        raise ValueError("Operation aborted")
    output = result.output
    if result.full_output_path:
        saved = Path(result.full_output_path)
        try:
            output = saved.read_text(encoding="utf-8", errors="replace")
        finally:
            saved.unlink(missing_ok=True)
    return output, result.returncode


def finish_search(
    output: str, notices: list[str], details: dict[str, Any], *, final_notice: str = ""
) -> ToolResult:
    truncation = truncate(output, max_lines=sys.maxsize)
    if truncation.truncated:
        notices.append(f"{format_size(MAX_BYTES)} limit reached")
        details["truncated"] = True
    if final_notice:
        notices.append(final_notice)
    output = truncation.content
    if notices:
        output += "\n\n[" + ". ".join(notices) + "]"
    return ToolResult(output, metadata=details or None)


class FindTool(WorkspaceTool):
    name = "find"
    description = "Search for files by glob pattern. Returns matching file paths relative to the search directory. Respects .gitignore. Output is truncated to 1000 results or 50KB (whichever is hit first)."

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files, e.g. '*.ts', '**/*.json', or 'src/**/*.spec.ts'",
                },
                "path": {"type": "string"},
                "limit": {"type": "number"},
            },
            "required": ["pattern"],
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        root = _resolve_authorized_path(self.workspace, args.get("path") or ".")
        _ensure_not_blocked(self.workspace, root)
        pattern = text_arg(args, "pattern")
        limit = int(number_arg(args, "limit", 1000))
        command = [
            "--glob",
            "--color=never",
            "--hidden",
            "--exclude",
            ".cade",
            "--max-results",
            str(limit),
        ]
        if not any((parent / ".git").exists() for parent in (root, *root.parents)):
            command.append("--no-require-git")
        if "/" in pattern:
            command.append("--full-path")
            if not pattern.startswith(("/", "**/")) and pattern != "**":
                pattern = "**/" + pattern
            if sys.platform == "win32":
                pattern = pattern.replace("/", r"[/\\]")
        output, code = _native(
            "fd",
            [*command, "--", pattern, str(root)],
            self.workspace,
            self.native_binary,
        )
        if code:
            raise ValueError(output.strip() or f"fd exited with code {code}")
        results: list[str] = []
        for line in output.splitlines():
            path = Path(line.strip())
            path = path if path.is_absolute() else root / path
            if _is_blocked(self.workspace, path):
                continue
            relative = path.relative_to(root).as_posix()
            results.append(relative + ("/" if line.endswith(("/", "\\")) else ""))
        if not results:
            return ToolResult("No files found matching pattern")
        notices: list[str] = []
        details: dict[str, Any] = {}
        if len(results) >= limit:
            notices.append(
                f"{limit} results limit reached. Use limit={limit * 2} for more, or refine pattern"
            )
            details["resultLimitReached"] = limit
        return finish_search("\n".join(results), notices, details)


class GrepTool(WorkspaceTool):
    name = "grep"
    description = "Search file contents for a pattern. Returns matching lines with file paths and line numbers. Respects .gitignore. Output is truncated to 100 matches or 50KB (whichever is hit first). Long lines are truncated to 500 chars."

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "ignoreCase": {"type": "boolean"},
                "literal": {"type": "boolean"},
                "context": {"type": "number"},
                "limit": {"type": "number"},
            },
            "required": ["pattern"],
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        root = _resolve_authorized_path(self.workspace, args.get("path") or ".")
        _ensure_not_blocked(self.workspace, root)
        if not root.exists():
            raise ValueError(f"Path not found: {root}")
        limit = max(1, int(number_arg(args, "limit", 100)))
        context = max(0, int(number_arg(args, "context", 0)))
        command = ["--json", "--line-number", "--color=never", "--hidden"]
        if args.get("ignoreCase"):
            command.append("--ignore-case")
        if args.get("literal"):
            command.append("--fixed-strings")
        if args.get("glob"):
            command.extend(("--glob", text_arg(args, "glob")))
        command.extend(
            (
                "--glob",
                "!.cade/**",
                "--glob",
                "!**/.cade/**",
                "--",
                text_arg(args, "pattern"),
                str(root),
            )
        )
        output, code = _native("rg", command, self.workspace, self.native_binary)
        if code not in (0, 1):
            raise ValueError(output.strip() or f"ripgrep exited with code {code}")
        matches: list[tuple[Path, int, str]] = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            raw = data["path"].get("text")
            if raw is None:
                continue
            path = Path(raw)
            if _is_blocked(self.workspace, path):
                continue
            matches.append((path, data["line_number"], data["lines"].get("text", "")))
            if len(matches) >= limit:
                break
        if not matches:
            return ToolResult("No matches found")
        lines: list[str] = []
        lines_truncated = False
        cache: dict[Path, list[str]] = {}
        for path, number, line in matches:
            relative = path.relative_to(root).as_posix() if root.is_dir() else path.name
            if context:
                if path not in cache:
                    try:
                        cache[path] = (
                            path.read_bytes()
                            .decode("utf-8", "replace")
                            .replace("\r\n", "\n")
                            .replace("\r", "\n")
                            .split("\n")
                        )
                    except OSError:
                        cache[path] = []
                content = cache[path]
                if not content:
                    lines.append(f"{relative}:{number}: (unable to read file)")
                    continue
                selected = [
                    (i, content[i - 1])
                    for i in range(
                        max(1, number - context),
                        min(len(content), number + context) + 1,
                    )
                ]
            else:
                selected = [
                    (
                        number,
                        line.replace("\r\n", "\n").replace("\r", "").removesuffix("\n"),
                    )
                ]
            for i, text in selected:
                if len(text) > 500:
                    text = text[:500] + "... [truncated]"
                    lines_truncated = True
                lines.append(
                    f"{relative}:{i}: {text}"
                    if i == number
                    else f"{relative}-{i}- {text}"
                )
        notices: list[str] = []
        details: dict[str, Any] = {}
        if len(matches) >= limit:
            notices.append(
                f"{limit} matches limit reached. Use limit={limit * 2} for more, or refine pattern"
            )
            details["matchLimitReached"] = limit
        if lines_truncated:
            details["linesTruncated"] = True
        return finish_search(
            "\n".join(lines),
            notices,
            details,
            final_notice="Some lines truncated to 500 chars. Use read tool to see full lines"
            if lines_truncated
            else "",
        )
