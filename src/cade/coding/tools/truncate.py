"""限制工具正文的行数和 UTF-8 字节数。"""

from dataclasses import dataclass

MAX_LINES = 2000
MAX_BYTES = 50 * 1024


def format_size(size: int) -> str:
    return f"{size / 1024:.1f}KB"


@dataclass(frozen=True)
class Truncation:
    content: str
    truncated: bool


def truncate(
    content: str, *, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES
) -> Truncation:
    limited = "".join(content.splitlines(keepends=True)[:max_lines])
    limited = limited.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return Truncation(limited, limited != content)
