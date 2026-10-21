"""模型与 Transport 解析器（单一事实源）。

只支持 Responses 协议：OpenAI API Key 使用 `openai_responses`，
ChatGPT 登录使用 `chatgpt`，DeepSeek API Key 使用 `deepseek_responses`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from cade.ai.models import normalize_model_id

# 别名映射：不区分大小写，映射到规范 model ID
MODEL_ALIASES: Final[dict[str, str]] = {
    "gpt-6": "gpt-6-sol",
    "gpt-5.6": "gpt-5.6-sol",
}

PROVIDER_TRANSPORTS: Final[dict[str, str]] = {
    "openai": "openai_responses",
    "chatgpt": "chatgpt",
    "deepseek": "deepseek_responses",
}


def provider_for_transport(transport: str) -> str:
    """把传输协议映射到独立的认证提供方。"""
    for provider, candidate in PROVIDER_TRANSPORTS.items():
        if candidate == transport:
            return provider
    raise ValueError(f"Unknown transport: {transport}")


# 默认 Base URL
DEFAULT_BASE_URLS: Final[dict[str, str]] = {
    "chatgpt": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
    # OpenAI SDK 会在 base_url 后追加 /responses。
    "deepseek_responses": "https://api.deepseek.com",
}

# 默认环境变量覆盖 Base URL
BASE_URL_ENV_VARS: Final[dict[str, str]] = {
    "openai_responses": "OPENAI_BASE_URL",
    "deepseek_responses": "DEEPSEEK_BASE_URL",
}


@dataclass(frozen=True)
class ModelResolution:
    """解析后的模型与传输配置。"""

    model: str
    provider: str
    transport: str
    default_base_url: str


class ModelResolver:
    """集中式模型与 Transport 解析领域服务。"""

    @staticmethod
    def resolve_alias(model: str) -> str:
        """规范化模型别名，若无别名则返回原名。"""
        stripped = model.strip()
        return MODEL_ALIASES.get(stripped.lower()) or normalize_model_id(stripped)

    @staticmethod
    def normalize_provider(provider: str | None) -> str | None:
        """规范化 provider 名称。"""
        if not provider:
            return None
        return provider.strip().lower()

    @classmethod
    def infer_provider(cls, model: str) -> str | None:
        """根据模型名称或前缀推断其归属 Provider。"""
        resolved_name = cls.resolve_alias(model).lower()

        if resolved_name.startswith(("gpt-", "chat-", "o1", "o3", "o4")):
            return "openai"
        if resolved_name.startswith("deepseek"):
            return "deepseek"

        from cade.ai.models import _MODELS

        for p_name, p_models in _MODELS.items():
            if resolved_name in p_models:
                return p_name

        return None

    @staticmethod
    def get_default_base_url(transport: str) -> str:
        """获取指定 transport 的默认 Base URL（优先读取环境变量覆盖）。"""
        env_var = BASE_URL_ENV_VARS.get(transport)
        if env_var and os.environ.get(env_var):
            return os.environ[env_var]
        return DEFAULT_BASE_URLS.get(transport, "")

    @classmethod
    def infer_transport(
        cls,
        model: str,
        provider: str | None = None,
        *,
        fallback_transport: str | None = None,
    ) -> str:
        """推断最合适的 transport。"""
        selected = cls.normalize_provider(provider) or cls.infer_provider(model)
        if selected in PROVIDER_TRANSPORTS:
            return PROVIDER_TRANSPORTS[selected]
        return fallback_transport or "openai_responses"

    @classmethod
    def resolve(
        cls,
        model_or_alias: str,
        provider: str | None = None,
        transport: str | None = None,
        *,
        fallback_transport: str | None = None,
    ) -> ModelResolution:
        """一站式解析模型名称、Provider、Transport 和 Base URL。"""
        raw_name = model_or_alias.strip()
        extracted_provider = provider
        if "/" in raw_name:
            p_prefix, m_part = raw_name.split("/", 1)
            extracted_provider = provider or p_prefix.strip()
            raw_name = m_part.strip()

        resolved_model = cls.resolve_alias(raw_name)
        if extracted_provider is None:
            if (
                fallback_transport == "chatgpt"
                and cls.infer_provider(resolved_model) == "openai"
            ):
                extracted_provider = "chatgpt"
            elif cls.infer_provider(resolved_model) is None and fallback_transport:
                extracted_provider = provider_for_transport(fallback_transport)
        norm_provider = (
            cls.normalize_provider(extracted_provider)
            or cls.infer_provider(resolved_model)
            or "openai"
        )
        if norm_provider not in PROVIDER_TRANSPORTS:
            supported = ", ".join(PROVIDER_TRANSPORTS)
            raise ValueError(
                f"Unsupported provider '{norm_provider}'. Supported: {supported}"
            )

        if transport:
            resolved_transport = transport
        else:
            resolved_transport = cls.infer_transport(
                model=resolved_model,
                provider=norm_provider,
                fallback_transport=fallback_transport,
            )

        default_base_url = cls.get_default_base_url(resolved_transport)

        return ModelResolution(
            model=resolved_model,
            provider=norm_provider,
            transport=resolved_transport,
            default_base_url=default_base_url,
        )
