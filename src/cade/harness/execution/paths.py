"""执行路径的统一解析，不隐式授予访问权限。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


def resolve_path(root: Path, raw: object) -> Path:
    """展开用户目录和符号链接，再相对固定工作区解析路径。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path is required")
    normalized = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]", " ", raw)
    normalized = normalized.removeprefix("@")
    if (
        sys.platform == "win32"
        and not normalized.startswith("//")
        and "\\" not in normalized
    ):
        match = re.fullmatch(
            r"/(?:mnt/|cygdrive/)?([a-z])(?:/(.*))?", normalized, re.IGNORECASE
        )
        if match:
            normalized = match[1].upper() + ":\\" + (match[2] or "").replace("/", "\\")
    if normalized.startswith("file://"):
        url = urlsplit(normalized)
        normalized = url2pathname(url.path)
        if url.netloc and url.netloc != "localhost":
            normalized = "//" + url.netloc + normalized
    if normalized == "~":
        normalized = str(Path.home())
    elif normalized.startswith("~/") or (
        sys.platform == "win32" and normalized.startswith("~\\")
    ):
        normalized = str(Path.home() / normalized[2:])
    candidate = Path(normalized)
    return (candidate if candidate.is_absolute() else root / candidate).resolve()
