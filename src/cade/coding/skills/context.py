"""从活动分支恢复被压缩或换窗移除的技能说明。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from cade.coding.skills.registry import SkillRegistry
from cade.coding.skills.tools import render_skill
from cade.harness.session.types import LogEntry, Message


def restore_skill_context(
    registry: SkillRegistry, entries: Sequence[LogEntry], visible: Sequence[Message]
) -> tuple[str, ...]:
    """历史中已加载但当前上下文缺失的技能重新进入请求上下文。"""
    names: set[str] = set()
    for entry in entries:
        if entry.type == "tool_result":
            artifact = entry.data.get("artifact")
            if isinstance(artifact, dict):
                metadata = artifact.get("tool_metadata")
                if isinstance(metadata, dict):
                    name = metadata.get("skill")
                    if isinstance(name, str):
                        names.add(name)
        if entry.type == "user":
            text = entry.data.get("text")
            if isinstance(text, str):
                names.update(re.findall(r'<skill name="([a-z0-9-]+)"', text))
    current = "\n".join(message.model_dump_json() for message in visible)
    restored: list[str] = []
    for name in sorted(names):
        skill = registry.load(name)
        if skill is not None:
            rendered = render_skill(skill)
            # JSON 转义后比较，避免换行和引号造成重复注入。
            if json.dumps(rendered, ensure_ascii=False)[1:-1] not in current:
                restored.append(rendered)
    return tuple(restored)
