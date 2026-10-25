"""全屏终端的滚动视口，不向终端历史写入活动记录。"""

from __future__ import annotations

from prompt_toolkit.formatted_text import StyleAndTextTuples

from .inline_renderer import _line_fragments


class FullscreenTranscript:
    """从完整行文档中选择可见窗口，支持 PageUp/PageDown 浏览。"""

    def __init__(self) -> None:
        self.scroll_from_bottom = 0

    def scroll(self, delta: int, total_lines: int, height: int) -> None:
        """delta 为正时向上浏览；为负时返回最新消息。"""
        maximum = max(0, total_lines - max(1, height))
        self.scroll_from_bottom = max(
            0, min(maximum, self.scroll_from_bottom + delta)
        )

    def prepare(self, lines: list[str], height: int) -> StyleAndTextTuples:
        """提供完整文档的可见尾部，始终保留在全屏渲染器内。"""
        height = max(1, height)
        self.scroll_from_bottom = min(
            self.scroll_from_bottom, max(0, len(lines) - height)
        )
        end = len(lines) - self.scroll_from_bottom
        selected = lines[max(0, end - height) : end]
        fragments = _line_fragments(selected)
        if fragments:
            fragments.pop()
        return fragments
