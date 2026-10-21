"""MCP stdio 与 HTTP 连接配置。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class McpServerConfig(BaseModel):
    """一个 MCP 服务的传输、凭证来源与超时配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transport: Literal["stdio", "streamable-http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    env_from: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers_from: dict[str, str] = Field(default_factory=dict)
    protocol: Literal["auto", "legacy"] = "auto"
    cwd: str | None = None
    timeout_ms: int = Field(default=30_000, gt=0, le=300_000)

    @model_validator(mode="after")
    def validate_transport(self) -> Self:
        """拒绝缺少连接目标或混用两种传输的配置。"""
        if self.transport == "stdio":
            if not self.command or not self.command.strip():
                raise ValueError("stdio requires a command")
            if self.url or self.headers_from:
                raise ValueError("stdio does not accept URL or headers")
        else:
            from urllib.parse import urlsplit

            url = urlsplit(self.url or "")
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("streamable-http requires an HTTP(S) URL")
            if url.username or url.password or url.query or url.fragment:
                raise ValueError("Use headers_from for credentials, not URL components")
            if self.command or self.args or self.env or self.env_from or self.cwd:
                raise ValueError("HTTP transport does not accept process settings")
        return self
