"""验证全屏显示窗口的滚动边界与历史内容。"""

from __future__ import annotations

from cade.hosts.tui.fullscreen_renderer import FullscreenTranscript


def _text(transcript: FullscreenTranscript, lines: list[str], height: int) -> str:
    return "".join(value for _, value in transcript.prepare(lines, height))


def test_fullscreen_shows_latest_lines_and_scrolls_by_pages() -> None:
    transcript = FullscreenTranscript()
    lines = [f"line {number}" for number in range(10)]

    assert _text(transcript, lines, 3) == "line 7\nline 8\nline 9"
    transcript.scroll(2, len(lines), 3)
    assert transcript.scroll_from_bottom == 2
    assert _text(transcript, lines, 3) == "line 5\nline 6\nline 7"

    transcript.scroll(100, len(lines), 3)
    assert transcript.scroll_from_bottom == 7
    assert _text(transcript, lines, 3) == "line 0\nline 1\nline 2"
    transcript.scroll(-100, len(lines), 3)
    assert _text(transcript, lines, 3) == "line 7\nline 8\nline 9"


def test_fullscreen_clamps_scrolling_after_transcript_shrinks() -> None:
    transcript = FullscreenTranscript()
    transcript.scroll(5, 10, 3)
    assert transcript.scroll_from_bottom == 5
    assert _text(transcript, ["new message"], 4) == "new message"
    assert transcript.scroll_from_bottom == 0


def test_fullscreen_empty_transcript_and_tiny_window() -> None:
    transcript = FullscreenTranscript()
    assert _text(transcript, [], 0) == ""
    assert _text(transcript, ["a", "b"], 0) == "b"
