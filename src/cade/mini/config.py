"""Cade Mini 的项目配置文件。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_CONFIG_NAME = "cade-mini.json"


class MiniConfig(BaseModel):
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

    @classmethod
    def load(
        cls,
        project_root: Path,
        explicit: Path | None = None,
    ) -> MiniConfig:
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
