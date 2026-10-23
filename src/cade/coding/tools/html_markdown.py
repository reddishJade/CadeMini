"""把 HTML 转为 Markdown，保留结构并省略活动内容。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

MAX_MARKDOWN_BYTES = 5 * 1024 * 1024
CONTENT_BYTES = MAX_MARKDOWN_BYTES - 64 * 1024
_OMITTED = {
    "script",
    "style",
    "noscript",
    "iframe",
    "object",
    "embed",
    "meta",
    "link",
    "template",
}
_BLOCKS = {
    "address",
    "article",
    "aside",
    "details",
    "dialog",
    "div",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "header",
    "main",
    "nav",
    "p",
    "section",
    "summary",
}
_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _slice_bytes(value: str, size: int) -> str:
    return value.encode("utf-8")[: max(0, size)].decode("utf-8", "ignore")


def _destination(value: str) -> str:
    return re.sub(r"[\t\n\r ]+", "%20", re.sub(r"([\\()])", r"\\\1", value))


def _title(value: str) -> str:
    escaped = re.sub(r'([\\"])', r"\\\1", re.sub(r"[\t\n\r ]+", " ", value).strip())
    return f' "{escaped}"' if value else ""


def _marker(name: str) -> str:
    return {
        "strong": "**",
        "b": "**",
        "em": "*",
        "i": "*",
        "s": "~~",
        "strike": "~~",
        "del": "~~",
    }.get(name, "")


@dataclass
class _Code:
    inline: bool
    text: str = ""
    language: str = ""


@dataclass
class _Link:
    href: str
    title: str


@dataclass
class _Marker:
    index: int
    block: int
    previous: _Marker | None
    leading_space: bool = False


@dataclass
class _List:
    ordered: bool
    next: int
    previous: _List | None


@dataclass
class _Item:
    indent: str
    previous: _Item | None


@dataclass
class _Table:
    start: int
    previous: _Table | None
    rows: list[list[str]] = field(default_factory=list)
    row: list[str] | None = None
    caption: str = ""
    fallback: bool = False


@dataclass
class _Details:
    open: bool
    previous: _Details | None
    summary: bool = False


@dataclass
class _Frame:
    name: str
    suppressed: bool
    link: _Link | None = None
    suspended_link: _Link | None = None
    marker: _Marker | None = None
    code: _Code | None = None
    link_code: _Code | None = None
    resumed_code: _Code | None = None
    list: _List | None = None
    item: _Item | None = None
    table: _Table | None = None
    cell: int | None = None
    caption: int | None = None
    details: _Details | None = None


class _Markdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.raw_indices: set[int] = set()
        self.stack: list[_Frame] = []
        self.pending_space = False
        self.pending_indent = ""
        self.last = ""
        self.quote_depth = 0
        self.needs_quote_prefix = False
        self.block_count = 0
        self.output_bytes = 0
        self.code: _Code | None = None
        self.link: _Link | None = None
        self.link_open = False
        self.marker: _Marker | None = None
        self.list: _List | None = None
        self.item: _Item | None = None
        self.table: _Table | None = None
        self.cell: int | None = None
        self.table_depth = 0
        self.details: _Details | None = None
        self.depth = 0
        self.stopped = False
        self.fallback_suppressed_depth = 0
        self.fallback_omitted_depth = 0

    def append(self, value: str, *, content: bool = False, raw: bool = False) -> None:
        limit = CONTENT_BYTES if content else MAX_MARKDOWN_BYTES
        value = _slice_bytes(value, limit - self.output_bytes)
        if not value:
            return
        if raw:
            self.raw_indices.add(len(self.output))
        self.output.append(value)
        self.output_bytes += len(value.encode("utf-8"))
        self.last = value[-1]

    def take(self, start: int) -> str:
        value = "".join(self.output[start:])
        del self.output[start:]
        self.raw_indices = {index for index in self.raw_indices if index < start}
        self.output_bytes -= len(value.encode("utf-8"))
        return value

    def quote_prefix(self) -> str:
        return "> " * min(8, self.quote_depth)

    def prefix_quote(self) -> None:
        if self.needs_quote_prefix and self.quote_depth and self.cell is None:
            self.append(self.quote_prefix())
            self.needs_quote_prefix = False

    def flush_space(self) -> None:
        if not self.pending_space:
            return
        marker = self.marker
        if (
            marker
            and len(self.output) == marker.index + 1
            and self.last not in {" ", "\n"}
        ):
            self.output[marker.index] = " " + self.output[marker.index]
            self.output_bytes += 1
            marker.leading_space = True
        elif self.last and self.last not in {" ", "\n"}:
            self.append(" ")
        self.pending_space = False

    def inline(self, value: str, *, opening: bool = False) -> None:
        if opening:
            self.flush_space()
        self.prefix_quote()
        if self.pending_indent:
            self.append(self.pending_indent)
            self.pending_indent = ""
        if self.link and not self.link_open:
            self.append("[")
            self.link_open = True
        self.append(value)

    def close_link(self, link: _Link) -> None:
        self.append(f"]({_destination(link.href)}{_title(link.title)})")

    def block(self) -> None:
        if self.link and self.link_open:
            self.close_link(self.link)
            self.link_open = False
        self.pending_space = False
        self.append("\n\n")
        self.block_count += 1
        self.needs_quote_prefix = self.quote_depth > 0
        self.pending_indent = self.item.indent if self.item else ""

    def suspend_link(self, frame: _Frame) -> None:
        if self.link:
            frame.suspended_link = self.link
            self.block()
            self.link = None
            self.link_open = False

    def text(self, value: str) -> None:
        if self.code:
            self.code.text += value
            return
        for part in re.split(r"([\t\n\f\r ]+)", value):
            if not part:
                continue
            if re.fullmatch(r"[\t\n\f\r ]+", part):
                self.pending_space = True
                continue
            self.flush_space()
            self.prefix_quote()
            if self.pending_indent:
                self.append(self.pending_indent)
                self.pending_indent = ""
            if self.link and not self.link_open:
                self.append("[")
                self.link_open = True
            escaped = re.sub(r"([\\`*_\[\]<>|])", r"\\\1", part).replace("~", r"\~")
            escaped = re.sub(r"^([#+-])", r"\\\1", escaped)
            escaped = re.sub(r"^(\d+)\.", r"\1\\.", escaped)
            self.append(escaped, content=True)

    def finish_code(self, code: _Code) -> None:
        if code.inline and not code.text:
            return
        ticks = max(
            (len(match[0]) for match in re.finditer(r"`+", code.text)), default=0
        )
        tildes = max(
            (len(match[0]) for match in re.finditer(r"~+", code.text)), default=0
        )
        if code.inline:
            fence = "`" * max(1, ticks + 1)
            self.flush_space()
            self.prefix_quote()
            available = max(0, CONTENT_BYTES - self.output_bytes - len(fence) * 2)
            payload = _slice_bytes(code.text, available)
            while payload:
                padding = (
                    " "
                    if (payload[0] in " `" or payload[-1] in " `")
                    and not re.fullmatch(" +", payload)
                    else ""
                )
                size = len(payload.encode())
                if size + len(padding) * 2 <= available:
                    self.append(fence + padding + payload + padding + fence, raw=True)
                    return
                payload = _slice_bytes(payload, size - 1)
            return
        if self.cell is not None:
            self.text(code.text)
            return
        marker = "`" if ticks <= tildes else "~"
        fence = marker * max(3, (ticks if marker == "`" else tildes) + 1)
        self.block()
        prefix = fence + code.language + "\n"
        payload = code.text
        while True:
            candidate = (
                prefix + payload + ("" if payload.endswith("\n") else "\n") + fence
            )
            value = (
                re.sub(r"^", self.quote_prefix(), candidate, flags=re.MULTILINE)
                if self.quote_depth
                else candidate
            )
            size = len(value.encode())
            if self.output_bytes + size <= CONTENT_BYTES:
                self.append(value, raw=True)
                self.block()
                return
            if not payload:
                return
            payload = _slice_bytes(
                payload,
                max(
                    0,
                    len(payload.encode())
                    - (size - max(0, CONTENT_BYTES - self.output_bytes)),
                ),
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag
        self.depth += 1
        if self.depth > 10000:
            if self.stack and self.stack[-1].suppressed:
                visible_parent = next(
                    (
                        i
                        for i in range(len(self.stack) - 1, -1, -1)
                        if not self.stack[i].suppressed
                    ),
                    -1,
                )
                self.fallback_suppressed_depth = visible_parent + 2
            self.code = None
            self.stopped = True
        if self.stopped:
            if name in _OMITTED:
                self.fallback_omitted_depth += 1
            else:
                self.pending_space = True
            if name in _VOID:
                self.handle_endtag(name)
            return
        attributes = {key: value or "" for key, value in attrs}
        # 与 htmlparser2 一样闭合省略结束标签的段落和列表项。
        close_names = (
            {"li"}
            if name == "li"
            else {"th", "td"}
            if name in {"th", "td"}
            else {"tr"}
            if name == "tr"
            else {"dt", "dd"}
            if name in {"dt", "dd"}
            else set()
        )
        if (
            name in _BLOCKS
            or name in {"ul", "ol", "pre", "blockquote", "table"}
            or re.fullmatch(r"h[1-6]", name)
        ):
            close_names.add("p")
        if self.stack and self.stack[-1].name in close_names:
            self.handle_endtag(self.stack[-1].name)
        suppressed = bool(self.stack and self.stack[-1].suppressed) or name in _OMITTED
        hidden = (
            "hidden" in attributes
            or attributes.get("aria-hidden", "").lower() == "true"
            or name == "head"
        )
        if hidden or (
            self.details
            and not self.details.open
            and not self.details.summary
            and name != "summary"
        ):
            suppressed = True
        frame = _Frame(name, suppressed)
        self.stack.append(frame)
        if not suppressed:
            self.open(frame, attributes)
        if name in _VOID:
            self.handle_endtag(name)

    def open(self, frame: _Frame, attributes: dict[str, str]) -> None:
        name = frame.name
        if self.code and not self.code.inline:
            if name == "br":
                self.code.text += "\n"
            if name == "code" and attributes.get("class"):
                language = re.search(
                    r"(?:language-|lang-)([^\s]+)", attributes["class"]
                )
                self.code.language = language[1] if language else ""
            return
        if name == "details":
            frame.details = _Details("open" in attributes, self.details)
            self.details = frame.details
            self.block()
        elif name == "summary":
            if self.details:
                self.details.summary = True
            self.block()
        elif name == "pre":
            self.suspend_link(frame)
            frame.code = self.code = _Code(False)
        elif name == "code":
            if not (self.code and self.code.inline):
                frame.code = self.code = _Code(True)
        elif re.fullmatch(r"h[1-6]", name):
            self.block()
            self.inline("#" * int(name[1]) + " ")
        elif name in _BLOCKS:
            self.suspend_link(frame)
            if name != "p" or self.last != " ":
                self.block()
        elif name == "br":
            self.pending_space = False
            self.inline("  \n")
            self.needs_quote_prefix = self.quote_depth > 0
        elif name == "hr":
            self.block()
            self.inline("---")
            self.block()
        elif marker := _marker(name):
            self.inline(marker, opening=True)
            frame.marker = self.marker = _Marker(
                len(self.output) - 1, self.block_count, self.marker
            )
        elif name == "a":
            if self.link and self.code and self.code.inline:
                parent = next(
                    (
                        item
                        for item in reversed(self.stack)
                        if item.link is self.link and item.link_code is self.code
                    ),
                    None,
                )
                if parent:
                    self.finish_code(self.code)
                    if self.link_open:
                        self.close_link(self.link)
                    self.code = parent.resumed_code
                    parent.link = parent.link_code = None
                    self.link = None
                    self.link_open = False
            if self.code and self.code.inline:
                frame.resumed_code = self.code
                if self.code.text:
                    self.finish_code(self.code)
                self.code.text = ""
                self.code = None
                frame.link = self.link = _Link(
                    attributes.get("href", ""), attributes.get("title", "")
                )
                self.link_open = True
                self.inline("[", opening=True)
                frame.link_code = self.code = _Code(True)
                return
            if self.link:
                if self.link_open:
                    self.close_link(self.link)
                for parent in reversed(self.stack):
                    if parent.link is self.link:
                        parent.link = None
                        break
            frame.link = self.link = _Link(
                attributes.get("href", ""), attributes.get("title", "")
            )
            self.link_open = True
            self.inline("[", opening=True)
        elif name == "img":
            alt = re.sub(r"([\\\]])", r"\\\1", attributes.get("alt", ""))
            close = f"]({_destination(attributes.get('src', ''))}{_title(attributes.get('title', ''))})"
            available = CONTENT_BYTES - self.output_bytes - len(("![" + close).encode())
            self.inline("![" + _slice_bytes(alt, available) + close, opening=True)
        elif name == "blockquote":
            self.suspend_link(frame)
            self.block()
            self.quote_depth += 1
            self.needs_quote_prefix = True
        elif name in {"ul", "ol"}:
            self.suspend_link(frame)
            start = re.match(r"^[+-]?\d+", attributes.get("start", "1").lstrip())
            frame.list = self.list = _List(
                name == "ol", int(start[0]) if start else 1, self.list
            )
            self.block()
        elif name == "li":
            self.block()
            value = re.match(r"^[+-]?\d+", attributes.get("value", "").lstrip())
            if self.list and self.list.ordered and value:
                self.list.next = int(value[0])
            marker = f"{self.list.next}." if self.list and self.list.ordered else "-"
            if self.list and self.list.ordered:
                self.list.next += 1
            prefix = (self.item.indent[:24] if self.item else "") + marker + " "
            frame.item = self.item = _Item(" " * len(prefix), self.item)
            self.pending_indent = ""
            self.inline(prefix)
        elif name == "table":
            self.suspend_link(frame)
            self.table_depth += 1
            if self.table_depth == 1:
                self.block()
                frame.table = self.table = _Table(len(self.output), self.table)
            else:
                self.pending_space = True
        elif name == "tr":
            if self.table_depth == 1 and self.table:
                self.table.row = []
            else:
                self.pending_space = True
        elif name in {"th", "td"}:
            if self.table_depth != 1:
                self.pending_space = True
                return
            if self.table and (attributes.get("colspan") or attributes.get("rowspan")):
                self.table.fallback = True
            frame.cell = self.cell = len(self.output)
        elif name == "caption":
            frame.caption = len(self.output)
        elif name == "dt":
            self.block()
            self.inline("**")
        elif name == "dd":
            self.inline("\n: ")

    def handle_data(self, data: str) -> None:
        if self.stopped:
            if self.fallback_suppressed_depth == self.fallback_omitted_depth == 0:
                self.text(data)
            return
        if not self.stack or not self.stack[-1].suppressed:
            self.text(data)

    def handle_endtag(self, tag: str) -> None:
        name = tag
        if self.stopped:
            self.depth -= 1
            if self.fallback_omitted_depth > 0 and name in _OMITTED:
                self.fallback_omitted_depth -= 1
            if (
                self.fallback_suppressed_depth > 0
                and self.depth < self.fallback_suppressed_depth
            ):
                self.fallback_suppressed_depth = 0
            return
        index = next(
            (
                i
                for i in range(len(self.stack) - 1, -1, -1)
                if self.stack[i].name == name
            ),
            None,
        )
        if index is None:
            return
        while len(self.stack) > index:
            self.depth -= 1
            frame = self.stack.pop()
            if not frame.suppressed:
                self.finish_frame(frame)

    def finish_frame(self, frame: _Frame) -> None:
        name = frame.name
        if frame.link_code:
            if self.link is not frame.link or not self.link_open:
                if frame.resumed_code:
                    frame.resumed_code.text += frame.link_code.text
                self.code = frame.resumed_code
                if self.link is frame.link:
                    self.link = None
                self.link_open = False
                return
            self.code = None
            self.finish_code(frame.link_code)
            if frame.link and self.link_open:
                self.close_link(frame.link)
            self.link = None
            self.link_open = False
            self.code = frame.resumed_code
            return
        if self.code and not self.code.inline and frame.code is None:
            return
        if frame.code:
            self.code = None
            self.finish_code(frame.code)
            if frame.suspended_link:
                self.link = frame.suspended_link
            return
        if name == "summary":
            if self.details:
                self.details.summary = False
            self.block()
        elif name == "details":
            self.details = frame.details.previous if frame.details else None
            self.block()
        elif name == "dt":
            self.inline("**")
        elif name == "dd":
            self.block()
        elif marker := _marker(name):
            trailing = self.pending_space
            self.pending_space = False
            if frame.marker:
                self.marker = frame.marker.previous
            if frame.marker and (
                frame.marker.block != self.block_count
                or len(self.output) == frame.marker.index + 1
            ):
                self.output[frame.marker.index] = ""
                self.pending_space = trailing or frame.marker.leading_space
                return
            self.inline(marker)
            self.pending_space = trailing or bool(
                frame.marker and frame.marker.leading_space
            )
        elif name == "a":
            if frame.link and (self.link is frame.link or self.link is None):
                if self.link_open or (self.last and self.last != "\n"):
                    self.close_link(frame.link)
                self.link_open = False
                self.link = None
        elif re.fullmatch(r"h[1-6]", name) or name in _BLOCKS:
            self.block()
            if frame.suspended_link:
                self.link = frame.suspended_link
        elif name == "blockquote":
            self.quote_depth -= 1
            self.block()
            if frame.suspended_link:
                self.link = frame.suspended_link
        elif name == "li":
            self.item = frame.item.previous if frame.item else None
            self.block()
        elif name in {"ul", "ol"}:
            self.list = frame.list.previous if frame.list else None
            self.block()
            if frame.suspended_link:
                self.link = frame.suspended_link
        elif name in {"th", "td"} and self.table_depth == 1:
            self.cell = None
            if frame.cell is not None:
                value = re.sub(r"[\t\r\n ]+", " ", self.take(frame.cell)).strip()
                value = re.sub(r"(?<!\\)\|", r"\|", value)
                if self.table and self.table.row is not None:
                    self.table.row.append(value)
        elif name == "tr" and self.table_depth == 1:
            if self.table and self.table.row is not None:
                self.table.rows.append(self.table.row)
                self.table.row = None
            self.pending_space = True
        elif name == "caption" and frame.caption is not None and self.table:
            self.table.caption = re.sub(
                r"[\t\r\n ]+", " ", self.take(frame.caption)
            ).strip()
        elif name == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                table = frame.table
                self.table = table.previous if table else None
                if table:
                    loose = re.sub(r"[\t\r\n ]+", " ", self.take(table.start)).strip()
                    if loose:
                        self.append(loose)
                        self.block()
                    if table.caption:
                        self.append(table.caption)
                        self.block()
                    width = len(table.rows[0]) if table.rows else 0
                    rectangular = width > 0 and all(
                        len(row) == width for row in table.rows
                    )
                    if not table.fallback and rectangular:
                        prefix = (
                            self.quote_prefix() if self.quote_depth else ""
                        ) + self.pending_indent
                        self.pending_indent = ""
                        self.append(
                            prefix
                            + "| "
                            + " | ".join(table.rows[0])
                            + " |\n"
                            + prefix
                            + "|"
                            + " --- |" * width
                        )
                        for row in table.rows[1:]:
                            self.append("\n" + prefix + "| " + " | ".join(row) + " |")
                    else:
                        for index, row in enumerate(table.rows):
                            if index:
                                self.block()
                            self.append(" | ".join(row))
                self.block()
                if frame.suspended_link:
                    self.link = frame.suspended_link
            else:
                self.pending_space = True

    def result(self) -> str:
        if self.stopped:
            self.stack.clear()
        while self.stack:
            self.handle_endtag(self.stack[-1].name)
        normalized: list[str] = []
        pending: list[str] = []

        def flush() -> None:
            value = "".join(pending)
            pending.clear()
            value = re.sub(
                r"[ \t]+\n",
                lambda match: "  \n" if match[0].startswith("  ") else "\n",
                value,
            )
            lines = re.sub(r"\n{3,}", "\n\n", value).split("\n")
            for index, line in enumerate(lines):
                if line or not 0 < index < len(lines) - 1:
                    continue
                before, after = (
                    re.match(r"^(?:> )+", lines[index - 1]),
                    re.match(r"^(?:> )+", lines[index + 1]),
                )
                if before and after and len(before[0]) != len(after[0]):
                    lines[index] = (
                        "> " * (min(len(before[0]), len(after[0])) // 2)
                    ).rstrip()
            normalized.append("\n".join(lines))

        for index, chunk in enumerate(self.output):
            if index in self.raw_indices:
                flush()
                normalized.append(chunk)
            else:
                pending.append(chunk)
        flush()
        return _slice_bytes("".join(normalized).strip(), MAX_MARKDOWN_BYTES)


def html_to_markdown(html: str) -> str:
    parser = _Markdown()
    for offset in range(0, len(html), 64 * 1024):
        parser.feed(html[offset : offset + 64 * 1024])
    parser.close()
    return parser.result()


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        skip = bool(self.stack and self.stack[-1][1]) or tag in {
            "script",
            "style",
            "noscript",
            "iframe",
            "object",
            "embed",
        }
        self.stack.append((tag, skip))
        if tag in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        index = next(
            (i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag),
            None,
        )
        if index is not None:
            del self.stack[index:]

    def handle_data(self, data: str) -> None:
        if not self.stack or not self.stack[-1][1]:
            self.output.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return "".join(parser.output).strip()
