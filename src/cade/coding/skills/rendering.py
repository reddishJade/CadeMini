"""把技能摘要格式化为模型可读的目录与激活说明。"""

from __future__ import annotations

import xml.sax.saxutils as _xml

from cade.coding.skills.models import SkillSummary

_CATALOG_DESCRIPTION_MAX_CHARS = 768


def format_skill_summary_xml(summary: SkillSummary) -> str:
    """将单个技能摘要格式化为 <skill> 块（pi 风格）。"""
    name = summary.name
    desc = summary.description
    if len(desc) > _CATALOG_DESCRIPTION_MAX_CHARS:
        desc = f"{desc[: _CATALOG_DESCRIPTION_MAX_CHARS - 3]}..."
    return (
        f"<skill>\n"
        f"  <name>{xml_escape_content(name)}</name>\n"
        f"  <description>{xml_escape_content(desc)}</description>\n"
        f"</skill>"
    )


def format_skill_list_xml(summaries: list[SkillSummary]) -> str:
    """格式化 <available-skills> 块（pi 风格）。"""
    if not summaries:
        return ""
    parts: list[str] = [
        "<skill-activation>",
        (
            "When the user task clearly matches a skill description below, "
            "call skill with that exact name before performing the task. "
            "Do not load a skill when no description clearly matches."
        ),
        "</skill-activation>",
        "<available-skills>",
    ]
    for s in summaries:
        parts.append(f"  {format_skill_summary_xml(s)}")
    parts.append("</available-skills>")
    return "\n".join(parts)


def xml_escape_content(text: str) -> str:
    """对 XML 元素内容进行转义。"""
    return _xml.escape(_xml_safe_text(text))


def xml_escape_attr(text: str) -> str:
    """对 XML 属性值进行转义（含引号）。返回带引号的字符串。"""
    return _xml.quoteattr(_xml_safe_text(text))


def _xml_safe_text(text: str) -> str:
    """替换 XML 1.0 不允许的控制字符。"""
    return "".join(
        character
        if character in "\t\n\r"
        or "\u0020" <= character <= "\ud7ff"
        or "\ue000" <= character <= "\ufffd"
        else "\ufffd"
        for character in text
    )
