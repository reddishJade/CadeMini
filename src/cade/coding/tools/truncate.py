"""按行数和 UTF-8 字节上限截取工具输出。"""

from dataclasses import asdict, dataclass
from typing import Any, Literal

MAX_LINES = 2000
MAX_BYTES = 50 * 1024


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


@dataclass(frozen=True)
class Truncation:
    content: str
    truncated: bool
    truncatedBy: Literal["lines", "bytes"] | None
    totalLines: int
    totalBytes: int
    outputLines: int
    outputBytes: int
    lastLinePartial: bool
    firstLineExceedsLimit: bool
    maxLines: int
    maxBytes: int

    def record(self) -> dict[str, Any]:
        return asdict(self)


def truncate(
    content: str,
    *,
    tail: bool = False,
    max_lines: int = MAX_LINES,
    max_bytes: int = MAX_BYTES,
) -> Truncation:
    lines = content.split("\n") if content else []
    if content.endswith("\n"):
        lines.pop()
    total_bytes = len(content.encode("utf-8"))
    truncated = len(lines) > max_lines or total_bytes > max_bytes
    first_exceeds = bool(
        truncated and not tail and lines and len(lines[0].encode()) > max_bytes
    )
    partial = False
    reason: Literal["lines", "bytes"] | None = None
    output = content
    count = len(lines)
    if truncated:
        chosen: list[str] = []
        used = 0
        reason = "lines"
        for line in reversed(lines) if tail else lines:
            if len(chosen) >= max_lines:
                break
            size = len(line.encode("utf-8")) + bool(chosen)
            if used + size > max_bytes:
                reason = "bytes"
                if tail and not chosen:
                    chosen.append(
                        line.encode("utf-8")[-max_bytes:].decode("utf-8", "ignore")
                    )
                    partial = True
                break
            chosen.append(line)
            used += size
        if tail:
            chosen.reverse()
        if len(chosen) >= max_lines:
            reason = "lines"
        output = "\n".join(chosen)
        count = len(chosen)
    return Truncation(
        output,
        truncated,
        reason,
        len(lines),
        total_bytes,
        count,
        len(output.encode("utf-8")),
        partial,
        first_exceeds,
        max_lines,
        max_bytes,
    )
