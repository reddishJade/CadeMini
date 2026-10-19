"""Provider 注册表与工厂。

Cade Mini 只支持 OpenAI Responses 协议，包括 API Key 与 ChatGPT 套餐登录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from cade_mini.ai.providers.base import ModelProvider
from cade_mini.ai.resolver import ModelResolver
from cade_mini.ai.types import ProviderConfig

from .responses import ChatGPTResponsesProvider, OpenAIResponsesProvider

# ── 注册表 ──

PROVIDER_REGISTRY: dict[str, type] = {
    "openai_responses": OpenAIResponsesProvider,
    "chatgpt": ChatGPTResponsesProvider,
}


# ── 配置类型 ──


class ModelProfileProto(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def transport(self) -> str: ...
    @property
    def chat_model(self) -> str: ...
    @property
    def base_url(self) -> str: ...
    @property
    def api_key(self) -> str: ...
    @property
    def context_window(self) -> int | None: ...
    @property
    def thinking(self) -> bool: ...
    @property
    def reasoning_effort(self) -> str | None: ...


@dataclass(frozen=True)
class ModelProfileConfig:
    provider: str = "openai"
    transport: str = "openai_responses"
    chat_model: str = ""
    base_url: str = ""
    api_key: str = ""
    context_window: int | None = None
    thinking: bool = True
    reasoning_effort: str | None = None


# ── 环境变量工具 ──

_OPENAI_API_KEY_ENV_VAR = "OPENAI_API_KEY"


def get_environment_api_key() -> str | None:
    """读取 OpenAI 环境凭据，不借用其他服务的 key。"""
    return os.environ.get(_OPENAI_API_KEY_ENV_VAR) or None


# ── 工厂 ──


def build_model_provider(profile: ModelProfileProto) -> ModelProvider:
    """按模型 profile 构造单个 provider 实例。"""
    transport = profile.transport
    provider_cls = PROVIDER_REGISTRY.get(transport)
    if provider_cls is None:
        raise ValueError(
            f"Unknown transport '{transport}'. "
            f"Available: {', '.join(PROVIDER_REGISTRY)}"
        )

    config = ProviderConfig(
        api_key=profile.api_key,
        model=profile.chat_model,
        base_url=profile.base_url or ModelResolver.get_default_base_url(transport),
        context_window=profile.context_window,
        thinking=profile.thinking,
        reasoning_effort=profile.reasoning_effort,
    )
    return provider_cls(config)
