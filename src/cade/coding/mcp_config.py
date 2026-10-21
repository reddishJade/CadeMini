"""MCP stdio 连接配置。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class McpServerConfig(BaseModel):
    """一个 stdio MCP server 的启动配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    timeout_ms: int = Field(default=30_000, gt=0, le=300_000)
