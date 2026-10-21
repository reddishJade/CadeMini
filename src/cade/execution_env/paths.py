"""执行路径的统一解析，不隐式授予访问权限。"""

from __future__ import annotations

from pathlib import Path


def resolve_path(root: Path, raw: object) -> Path:
    """展开用户目录和符号链接，再相对固定工作区解析路径。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path is required")
    candidate = Path(raw.strip()).expanduser()
    return (candidate if candidate.is_absolute() else root / candidate).resolve()
