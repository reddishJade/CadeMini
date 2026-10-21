"""约束五层依赖方向与执行契约的归属。"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "src" / "cade"
_ALLOWED = {
    "ai": {"ai"},
    "harness": {"ai", "harness"},
    "coding": {"ai", "harness", "coding"},
    "app": {"ai", "harness", "coding", "app"},
    "hosts": {"ai", "harness", "coding", "app", "hosts"},
}


def _imports(path: Path) -> list[str]:
    module = "cade." + ".".join(path.relative_to(_ROOT).with_suffix("").parts)
    package = module.rpartition(".")[0]
    imports: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = importlib.util.resolve_name("." * node.level + name, package)
            imports.append(name)
            if name == "cade":
                imports.extend(f"cade.{alias.name}" for alias in node.names)
    return imports


@pytest.mark.parametrize("layer", _ALLOWED)
def test_layer_dependencies_point_downward(layer: str) -> None:
    violations: list[str] = []
    for path in (_ROOT / layer).rglob("*.py"):
        for name in _imports(path):
            if name.startswith("cade.") and name.split(".")[1] not in _ALLOWED[layer]:
                violations.append(f"{path.relative_to(_ROOT)} -> {name}")
    assert violations == []


def test_only_five_runtime_packages_exist() -> None:
    packages = {
        path.name
        for path in _ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    assert packages == set(_ALLOWED)


def test_execution_contracts_do_not_import_local_backends() -> None:
    paths = [
        _ROOT / "harness" / "loop.py",
        _ROOT / "harness" / "permissions.py",
        _ROOT / "harness" / "executor.py",
        _ROOT / "harness" / "execution" / "scope.py",
    ]
    for path in paths:
        assert not any(
            name.startswith("cade.harness.execution.local") for name in _imports(path)
        ), path
