"""敏感路径识别：凭据目录、凭据文件和环境文件。"""

from __future__ import annotations

from pathlib import Path

# 只覆盖常见的凭据位置；未列出的路径仍由沙箱边界约束。
_CREDENTIAL_PREFIXES: tuple[tuple[str, ...], ...] = (
    (".ssh",),
    (".aws",),
    (".gnupg",),
    (".kube",),
    (".docker",),
    (".config", "gh"),
    (".config", "gcloud"),
    (".config", "hub"),
    (".git-credentials",),
    (".netrc",),
    (".npmrc",),
    (".pypirc",),
)


def is_environment_file(path: Path) -> bool:
    """判断路径名是否为需要审批的环境文件。"""
    return path.name.startswith(".env") and path.name != ".env.example"


def is_sensitive_path(path: Path, home: Path | None = None) -> bool:
    """判断路径是否位于凭据目录，或是环境文件。"""
    resolved = path.expanduser().resolve()
    if is_environment_file(resolved):
        return True
    root = (home or Path.home()).expanduser().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return False
    return any(
        _matches_prefix(relative.parts, prefix) for prefix in _CREDENTIAL_PREFIXES
    )


def sensitive_roots(
    workspace: Path | None = None,
    home: Path | None = None,
) -> tuple[Path, ...]:
    """返回凭据根目录和当前工作区的环境文件。"""
    root = (home or Path.home()).expanduser().resolve()
    paths = [root.joinpath(*prefix) for prefix in _CREDENTIAL_PREFIXES]
    if workspace is not None:
        workspace = workspace.resolve()
        paths.extend(path for path in workspace.glob(".env*") if path.is_file())
    return tuple(path.resolve() for path in paths)


def contains_sensitive_path(
    path: Path,
    workspace: Path | None = None,
    home: Path | None = None,
) -> bool:
    """判断路径本身或其后代是否包含敏感路径。"""
    resolved = path.expanduser().resolve()
    if is_sensitive_path(resolved, home):
        return True
    return any(
        root == resolved or root.is_relative_to(resolved)
        for root in sensitive_roots(workspace, home)
    )


def is_sensitive_reason(reason: str) -> bool:
    """判断审批理由是否来自敏感路径检查。"""
    lowered = reason.lower()
    return "sensitive path" in lowered or "sensitive data" in lowered


def _matches_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(parts) >= len(prefix) and parts[: len(prefix)] == prefix
