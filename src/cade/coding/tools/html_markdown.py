"""用解析库清理网页，并生成 Markdown 或纯文本。"""

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

MAX_MARKDOWN_BYTES = 5 * 1024 * 1024


def _clean(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select(
        "script, style, noscript, iframe, object, embed, template"
    ):
        element.decompose()
    return soup


def html_to_markdown(html: str) -> str:
    return MarkdownConverter(heading_style="ATX").convert_soup(_clean(html)).strip()


def html_to_text(html: str) -> str:
    return _clean(html).get_text(" ", strip=True)
