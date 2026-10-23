"""批量替换、模糊匹配和差异格式。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _Component:
    count: int
    kind: str
    previous: _Component | None


@dataclass
class _Path:
    old_pos: int
    last: _Component | None


def _line_changes(before: str, after: str) -> list[tuple[str, list[str]]]:
    """使用确定的 Myers 路径选择，保持重复行的差异顺序。"""
    old = re.findall(r"[^\n]*\n|[^\n]+", before)
    new = re.findall(r"[^\n]*\n|[^\n]+", after)

    def common(path: _Path, diagonal: int) -> int:
        old_pos = path.old_pos
        new_pos = old_pos - diagonal
        count = 0
        while (
            new_pos + 1 < len(new)
            and old_pos + 1 < len(old)
            and old[old_pos + 1] == new[new_pos + 1]
        ):
            new_pos += 1
            old_pos += 1
            count += 1
        if count:
            path.last = _Component(count, " ", path.last)
        path.old_pos = old_pos
        return new_pos

    def extend(path: _Path, kind: str) -> _Path:
        last = path.last
        component = (
            _Component(last.count + 1, kind, last.previous)
            if last and last.kind == kind
            else _Component(1, kind, last)
        )
        return _Path(path.old_pos + (kind == "-"), component)

    base = _Path(-1, None)
    new_pos = common(base, 0)
    paths: dict[int, _Path] = {0: base}
    min_diagonal = -len(old) - len(new)
    max_diagonal = -min_diagonal
    complete = base.old_pos + 1 >= len(old) and new_pos + 1 >= len(new)
    for length in range(1, len(old) + len(new) + 1):
        if complete:
            break
        for diagonal in range(
            max(min_diagonal, -length), min(max_diagonal, length) + 1, 2
        ):
            removed = paths.pop(diagonal - 1, None)
            added = paths.get(diagonal + 1)
            can_add = added is not None and 0 <= added.old_pos - diagonal < len(new)
            can_remove = removed is not None and removed.old_pos + 1 < len(old)
            if not can_add and not can_remove:
                paths.pop(diagonal, None)
                continue
            if added is not None and (
                not can_remove
                or (can_add and removed is not None and removed.old_pos < added.old_pos)
            ):
                base = extend(added, "+")
            elif removed is not None:
                base = extend(removed, "-")
            else:
                continue
            new_pos = common(base, diagonal)
            if base.old_pos + 1 >= len(old) and new_pos + 1 >= len(new):
                complete = True
                break
            paths[diagonal] = base
            if base.old_pos + 1 >= len(old):
                max_diagonal = min(max_diagonal, diagonal - 1)
            if new_pos + 1 >= len(new):
                min_diagonal = max(min_diagonal, diagonal + 1)
    components: list[_Component] = []
    last = base.last
    while last is not None:
        components.append(last)
        last = last.previous
    result: list[tuple[str, list[str]]] = []
    old_pos = new_pos = 0
    for part in reversed(components):
        if part.kind == "-":
            lines = old[old_pos : old_pos + part.count]
            old_pos += part.count
        else:
            lines = new[new_pos : new_pos + part.count]
            new_pos += part.count
            if part.kind == " ":
                old_pos += part.count
        result.append((part.kind, lines))
    return result


def _unified_patch(path: str, changes: list[tuple[str, list[str]]]) -> str:
    parts = [*changes, (" ", [])]
    old_start = new_start = 0
    old_line = new_line = 1
    current: list[str] = []
    output = [f"--- {path}", f"+++ {path}"]
    for index, (kind, lines) in enumerate(parts):
        if kind != " ":
            if not old_start:
                old_start, new_start = old_line, new_line
                previous = parts[index - 1][1] if index else []
                current = [" " + line for line in previous[-4:]]
                old_start -= len(current)
                new_start -= len(current)
            current.extend(kind + line for line in lines)
            if kind == "+":
                new_line += len(lines)
            else:
                old_line += len(lines)
        else:
            if old_start:
                if len(lines) <= 8 and index < len(parts) - 2:
                    current.extend(" " + line for line in lines)
                else:
                    context = min(len(lines), 4)
                    current.extend(" " + line for line in lines[:context])
                    old_count, new_count = (
                        old_line - old_start + context,
                        new_line - new_start + context,
                    )
                    output.append(
                        f"@@ -{old_start - (old_count == 0)},{old_count} +{new_start - (new_count == 0)},{new_count} @@"
                    )
                    for line in current:
                        output.append(line.removesuffix("\n"))
                        if not line.endswith("\n"):
                            output.append("\\ No newline at end of file")
                    old_start = new_start = 0
                    current = []
            old_line += len(lines)
            new_line += len(lines)
    return "\n".join(output) + "\n"


def diff_details(path: str, before: str, after: str) -> dict[str, str | int]:
    changes = _line_changes(before, after)
    width = len(str(max(len(before.split("\n")), len(after.split("\n")))))
    output: list[str] = []
    first_changed = 1
    changed = False
    old_line = new_line = 1
    for index, (kind, raw_lines) in enumerate(changes):
        lines = [line.removesuffix("\n") for line in raw_lines]
        if kind != " ":
            if not changed:
                first_changed = new_line
                changed = True
            for line in lines:
                number = new_line if kind == "+" else old_line
                output.append(f"{kind}{number:>{width}} {line}")
                if kind == "+":
                    new_line += 1
                else:
                    old_line += 1
        else:
            leading = changed
            trailing = index < len(changes) - 1
            if not leading and not trailing:
                old_line += len(lines)
                new_line += len(lines)
                continue
            selected = set(range(min(len(lines), 4))) if leading else set()
            if trailing:
                selected.update(range(max(0, len(lines) - 4), len(lines)))
            previous = -1
            for number in sorted(selected):
                if number > previous + 1:
                    output.append(f" {'':>{width}} ...")
                output.append(f" {old_line + number:>{width}} {lines[number]}")
                previous = number
            if previous < len(lines) - 1:
                output.append(f" {'':>{width}} ...")
            old_line += len(lines)
            new_line += len(lines)
    return {
        "diff": "\n".join(output),
        "patch": _unified_patch(path, changes),
        "firstChangedLine": first_changed,
    }
