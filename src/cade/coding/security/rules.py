"""工作区权限规则的数据契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RuleConfig(BaseModel):
    """一条静态权限规则；同层冲突按 deny、ask、allow 排序。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    decision: Literal["allow", "deny", "ask"]
    path_prefix: str | None = None
    command_prefix: str | None = None
