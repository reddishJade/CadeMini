"""Cade Mini 文档基建的回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade.mini import docs


def _manifest(pages: list[str]) -> dict[str, object]:
    return {
        "title": "Test Docs",
        "description": "Test description",
        "sections": [{"title": "Start", "pages": pages}],
    }


def _write_docs(root: Path, pages: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "docs.json").write_text(
        json.dumps(_manifest(list(pages))),
        encoding="utf-8",
    )
    for relative, text in pages.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    docs.write_llms(root)


def test_repository_docs_check_passes() -> None:
    assert docs.check(docs._DOCS_ROOT) == []


def test_llms_lists_every_manifest_page() -> None:
    catalog = docs.render_llms(docs._DOCS_ROOT)

    assert "# Cade Mini" in catalog
    assert "(quickstart.md)" in catalog
    assert "(security" not in catalog
    assert "(tools-security.md)" in catalog


def test_llms_full_contains_page_bodies() -> None:
    catalog = docs.render_llms_full(docs._DOCS_ROOT)

    assert "<!-- source: architecture.md -->" in catalog
    assert "The session log is the source of truth" in catalog


def test_stale_llms_full_is_reported(tmp_path: Path) -> None:
    _write_docs(tmp_path, {"README.md": "# Test\n\nOriginal.\n"})
    (tmp_path / "README.md").write_text("# Test\n\nChanged.\n", encoding="utf-8")

    issues = docs.check(tmp_path)

    assert any("llms-full.txt: out of date" in issue for issue in issues)


def test_broken_link_is_reported(tmp_path: Path) -> None:
    _write_docs(
        tmp_path,
        {"README.md": "# Test\n\n[missing](missing.md)\n"},
    )

    issues = docs.check(tmp_path)

    assert any("missing link target" in issue for issue in issues)


def test_unlisted_markdown_is_reported(tmp_path: Path) -> None:
    _write_docs(tmp_path, {"README.md": "# Test\n"})
    (tmp_path / "extra.md").write_text("# Extra\n", encoding="utf-8")

    issues = docs.check(tmp_path)

    assert any("not listed" in issue for issue in issues)


def test_duplicate_anchor_is_reported(tmp_path: Path) -> None:
    _write_docs(
        tmp_path,
        {"README.md": "# Test\n\n## Same\n\n## Same\n"},
    )

    issues = docs.check(tmp_path)

    assert any("duplicate heading anchor" in issue for issue in issues)


def test_build_renders_site(tmp_path: Path) -> None:
    pytest.importorskip("markdown_it")
    _write_docs(
        tmp_path,
        {
            "README.md": "# Test\n\nHello docs. [Quickstart](quickstart.md)\n",
            "quickstart.md": "# Quickstart\n\nGo.\n",
        },
    )
    out = tmp_path / "site" / "index.html"

    path = docs.build(tmp_path, out)

    text = path.read_text(encoding="utf-8")
    assert "<title>Test Docs</title>" in text
    assert "Hello docs." in text
    assert "page-readme" in text
    assert 'href="#page-quickstart"' in text


def test_cli_check_and_llms(tmp_path: Path) -> None:
    _write_docs(tmp_path, {"README.md": "# Test\n"})

    assert docs.main(["--root", str(tmp_path), "check"]) == 0
    assert docs.main(["--root", str(tmp_path), "llms"]) == 0
