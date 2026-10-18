"""Cade Mini 的 import firewall 回归测试。"""

from __future__ import annotations

import ast
from pathlib import Path

_ALLOWED = (
    "cade.ai",
    "cade.harness.execution_env",
    "cade.mini",
)


def test_mini_imports_stay_inside_allowed_layers() -> None:
    root = Path(__file__).resolve().parents[1] / "mini"
    violations: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    # 相对 import 只会在 Mini 包内移动。
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if not name.startswith("cade"):
                    continue
                if name == "cade" or not any(
                    name == allowed or name.startswith(allowed + ".")
                    for allowed in _ALLOWED
                ):
                    violations.append((path.as_posix(), name))

    assert not violations, violations
