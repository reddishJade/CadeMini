"""批量替换、模糊匹配和差异格式。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import unified_diff
from itertools import pairwise


def normalize_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_fuzzy(text: str) -> str:
    whitespace = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
    text = "\n".join(
        line.rstrip(whitespace)
        for line in unicodedata.normalize("NFKC", text).split("\n")
    )
    return text.translate(
        str.maketrans(
            {
                **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"),
                **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),
                **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
                **dict.fromkeys(
                    "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000",
                    " ",
                ),
            }
        )
    )


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
    fuzzy = normalize_fuzzy(content)
    use_fuzzy = any(
        edit.old not in content and normalize_fuzzy(edit.old) in fuzzy for edit in edits
    )
    base = fuzzy if use_fuzzy else content
    matches: list[_Match] = []
    for index, edit in enumerate(edits):
        needle = edit.old if edit.old in base else normalize_fuzzy(edit.old)
        start = base.find(needle)
        if start < 0:
            label = f"edits[{index}]" if multiple else "the exact text"
            old = "oldText" if multiple else "old text"
            raise ValueError(
                f"Could not find {label} in {path}. The {old} must match exactly including all whitespace and newlines."
            )
        normalized_base = normalize_fuzzy(base)
        normalized_old = normalize_fuzzy(edit.old)
        occurrences = (
            normalized_base.count(normalized_old)
            if normalized_old
            else len(normalized_base.encode("utf-16-le")) // 2 - 1
        )
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
    if use_fuzzy:
        # 只从模糊视图重写命中的行，其他行保留原文。
        original_lines = re.findall(r"[^\n]*\n|[^\n]+", content)
        base_lines = re.findall(r"[^\n]*\n|[^\n]+", base)
        if len(original_lines) != len(base_lines):
            raise ValueError(
                "Cannot preserve unchanged lines because the base content has a different line count."
            )
        starts: list[int] = []
        offset = 0
        for line in base_lines:
            starts.append(offset)
            offset += len(line)
        groups: list[tuple[int, int, list[_Match]]] = []
        for match in matches:
            first = next(
                (
                    i
                    for i, start in enumerate(starts)
                    if start <= match.start < start + len(base_lines[i])
                ),
                None,
            )
            if first is None:
                raise ValueError("Replacement range is outside the base content.")
            last = first + 1
            while (
                starts[last - 1] + len(base_lines[last - 1])
                < match.start + match.length
            ):
                last += 1
            if groups and first < groups[-1][1]:
                prior_first, prior_last, prior_matches = groups.pop()
                groups.append(
                    (prior_first, max(last, prior_last), [*prior_matches, match])
                )
            else:
                groups.append((first, last, [match]))
        pieces: list[str] = []
        cursor = 0
        for first, last, group in groups:
            pieces.append("".join(original_lines[cursor:first]))
            pieces.append(
                _replace("".join(base_lines[first:last]), group, starts[first])
            )
            cursor = last
        pieces.append("".join(original_lines[cursor:]))
        result = "".join(pieces)
    else:
        result = _replace(base, matches)
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
