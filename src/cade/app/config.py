"""Cade 的项目配置文件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_CONFIG_NAME = "cade.json"


class RuleConfig(BaseModel):
    """一条静态权限规则；同层冲突按 deny、ask、allow 排序。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    decision: Literal["allow", "deny", "ask"]
    path_prefix: str | None = None
    command_prefix: str | None = None


class McpServerConfig(BaseModel):
    """一个 stdio MCP server 的启动配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    timeout_ms: int = Field(default=30_000, gt=0, le=300_000)


class CadeConfig(BaseModel):
    """项目级默认值；命令行参数优先于配置文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_steps: int | None = Field(default=None, gt=0)
    sessions_dir: str | None = None
    read_only: bool = False
    instructions: bool = True
    note: bool = True
    memory: bool = True
    auto_review: bool = False
    prompt_modules: list[str] | None = None
    mode: Literal["plan", "build", "ask"] = "build"
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = (
        "workspace-write"
    )
    network: bool = True
    rules: list[RuleConfig] = Field(default_factory=list)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)

    @classmethod
    def load(
        cls,
        project_root: Path,
        explicit: Path | None = None,
    ) -> CadeConfig:
        """读取项目配置；显式路径不存在时报错，默认路径缺失时用默认值。"""
        root = project_root.resolve()
        if explicit is not None:
            path = explicit if explicit.is_absolute() else root / explicit
            if not path.is_file():
                raise ValueError(f"config file not found: {path}")
        else:
            path = root / DEFAULT_CONFIG_NAME
            if not path.is_file():
                return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read config {path}: {exc}") from exc
        if not isinstance(raw, dict):
            # 这不是 Python 类型误用，而是配置文件内容不合法。
            raise ValueError(f"config must be a JSON object: {path}")  # noqa: TRY004
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"invalid config {path}: {exc}") from exc
