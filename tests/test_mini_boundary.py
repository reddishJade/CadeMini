"""独立包的导入边界回归测试。"""

from __future__ import annotations

import ast
from pathlib import Path

import cade_mini


def test_package_imports_do_not_depend_on_cade() -> None:
    root = Path(cade_mini.__file__).resolve().parent
    files = sorted(root.rglob("*.py"))
    assert files
    violations: list[tuple[str, str]] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "cade" or name.startswith("cade."):
                    violations.append((path.as_posix(), name))
                if (
                    path.relative_to(root).parts[0] in {"ai", "execution_env"}
                    and name.startswith("cade_mini.")
                    and not name.startswith(
                        ("cade_mini.ai.", "cade_mini.execution_env.")
                    )
                ):
                    violations.append((path.as_posix(), name))

    assert not violations, violations


def test_package_has_no_public_reexports() -> None:
    assert not hasattr(cade_mini, "__all__")
    assert not hasattr(cade_mini, "SessionLog")
    assert not hasattr(cade_mini, "build_tools")
