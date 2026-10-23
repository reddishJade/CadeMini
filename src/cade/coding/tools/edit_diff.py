"""精确批量替换和标准差异预览。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from itertools import pairwise


def normalize_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class Edit:
    old: str
    new: str


@dataclass(frozen=True)
class _Match:
    edit: int
    start: int
    length: int
    text: str


def _replace(content: str, matches: list[_Match], offset: int = 0) -> str:
    for match in reversed(matches):
        start = match.start - offset
        content = content[:start] + match.text + content[start + match.length :]
    return content


def apply_edits(content: str, edits: list[Edit], path: str) -> str:
    edits = [Edit(normalize_lf(edit.old), normalize_lf(edit.new)) for edit in edits]
    multiple = len(edits) > 1
    for index, edit in enumerate(edits):
        if not edit.old:
            label = f"edits[{index}].oldText" if multiple else "oldText"
            raise ValueError(f"{label} must not be empty in {path}.")
    matches: list[_Match] = []
    for index, edit in enumerate(edits):
        needle = edit.old
        start = content.find(needle)
        if start < 0:
            label = f"edits[{index}]" if multiple else "the exact text"
            old = "oldText" if multiple else "old text"
            raise ValueError(
                f"Could not find {label} in {path}. The {old} must match exactly including all whitespace and newlines."
            )
        occurrences = content.count(needle)
        if occurrences > 1:
            label = f"edits[{index}]" if multiple else "the text"
            unique = "Each oldText" if multiple else "The text"
            raise ValueError(
                f"Found {occurrences} occurrences of {label} in {path}. {unique} must be unique. Please provide more context to make it unique."
            )
        matches.append(_Match(index, start, len(needle), edit.new))
    matches.sort(key=lambda match: match.start)
    for previous, current in pairwise(matches):
        if previous.start + previous.length > current.start:
            raise ValueError(
                f"edits[{previous.edit}] and edits[{current.edit}] overlap in {path}. Merge them into one edit or target disjoint regions."
            )
    result = _replace(content, matches)
    if result == content:
        message = (
            "The replacements produced identical content."
            if multiple
            else (
                "The replacement produced identical content. This might indicate an issue with special characters or the text not existing as expected."
            )
        )
        raise ValueError(f"No changes made to {path}. {message}")
    return result


def diff_details(path: str, before: str, after: str) -> dict[str, str | int]:
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    first = next(
        (
            index
            for index, (left, right) in enumerate(zip(old, new, strict=False), 1)
            if left != right
        ),
        min(len(old), len(new)) + 1,
    )
    return {
        "patch": "".join(unified_diff(old, new, fromfile=path, tofile=path)),
        "firstChangedLine": first,
    }
