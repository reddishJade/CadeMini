"""把 cade_mini.ai 的 provider 适配到 Mini 的循环协议。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cade_mini.ai.providers.base import ModelProvider
from cade_mini.ai.providers.registry import (
    ModelProfileConfig,
    build_model_provider,
    get_environment_api_key,
)
from cade_mini.ai.resolver import ModelResolver
from cade_mini.app.auth import JsonCredentialStore, resolve_codex_credential

DEFAULT_PROVIDER = "openai-codex"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "high"


@dataclass(frozen=True)
class ProviderConfig:
    """启动时选择的 provider 与模型；连接参数与认证分开。"""

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    base_url: str = ""
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT
    thinking: bool = True
    api_key: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not self.model.strip():
            raise ValueError("model must not be empty")


def build_provider(
    config: ProviderConfig | None = None,
    *,
    auth_path: Path | None = None,
) -> ModelProvider:
    """解析模型、连接和凭据，返回 cade_mini.ai provider。"""
    selected = config or ProviderConfig()
    resolution = ModelResolver.resolve(
        selected.model,
        provider=selected.provider,
    )
    transport = resolution.transport
    provider = resolution.provider
    base_url = selected.base_url or resolution.default_base_url
    api_key = selected.api_key
    account_id: str | None = None
    store = JsonCredentialStore(auth_path)

    if transport == "openai_codex":
        credential = resolve_codex_credential(
            store,
            provider="openai-codex",
        )
        if credential is None or credential.type != "oauth" or not credential.access:
            raise RuntimeError(
                "Missing openai-codex login; run 'cade-mini login' first"
            )
        api_key = credential.access
        account_id = credential.account_id
    else:
        stored = store.get(provider)
        if (
            api_key is None
            and stored is not None
            and stored.type == "api_key"
            and stored.access
        ):
            api_key = stored.access
        api_key = api_key or get_environment_api_key()
        if not api_key:
            raise RuntimeError(
                f"Missing API key for provider {provider}; "
                f"run 'cade-mini login --provider {provider} --method api_key' "
                "or set OPENAI_API_KEY"
            )

    profile = ModelProfileConfig(
        provider=provider,
        transport=transport,
        chat_model=resolution.model,
        base_url=base_url,
        api_key=api_key,
        thinking=selected.thinking,
        reasoning_effort=selected.reasoning_effort,
        account_id=account_id,
    )
    return build_model_provider(profile)
