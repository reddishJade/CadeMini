"""首次启动配置向导；复用 Cade Mini 的配置与凭据服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cade_mini.ai.models import get_models
from cade_mini.ai.providers.registry import get_environment_api_key
from cade_mini.ai.resolver import DEFAULT_BASE_URLS, PROVIDER_TRANSPORTS
from cade_mini.hosts.interaction_settings import (
    AuthenticationService,
    SettingsService,
)

CONFIG_FILENAME = "cade-mini.json"
AUTH_METHOD_ACCOUNT = "Sign in with an account"
AUTH_METHOD_API_KEY = "Sign in with an API key"
LOGIN_CHOICE_AUTH = AUTH_METHOD_ACCOUNT
LOGIN_CHOICE_API = AUTH_METHOD_API_KEY

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "chatgpt": {
        "label": "ChatGPT account",
        "transport": "chatgpt",
        "model": "gpt-6-sol",
    },
    "openai": {
        "label": "OpenAI API key",
        "transport": "openai_responses",
        "model": "gpt-6-sol",
    },
    "deepseek": {
        "label": "DeepSeek API key",
        "transport": "deepseek_responses",
        "model": "deepseek-flash",
    },
}
OPENAI_MODELS = [model.id for model in get_models("openai")]


def default_base_url(preset: dict[str, Any]) -> str:
    """返回 provider 的默认 Base URL。"""
    transport = str(preset.get("transport", ""))
    return DEFAULT_BASE_URLS.get(transport, "")


def env_api_key(preset: dict[str, Any]) -> str | None:
    """读取 provider 对应环境变量中的 API Key。"""
    provider = str(preset.get("provider", ""))
    return get_environment_api_key(provider) if provider else None


def save_api_key_connection(provider_key: str, api_key: str, base_url: str) -> Path:
    """保存 API Key 并返回项目配置文件路径。"""
    del base_url
    auth = AuthenticationService()
    auth.save_api_key(provider_key, api_key)
    settings = SettingsService(Path.cwd())
    preset = PROVIDER_PRESETS[provider_key]
    settings.save_many(
        {
            "provider": provider_key,
            "model": preset["model"],
        }
    )
    return settings.path


def run_setup_wizard(config_path: Path | None = None) -> Path | None:
    """交互式选择 provider、模型与 API Key。"""
    import questionary

    provider = questionary.select(
        "Choose a provider:",
        choices=list(PROVIDER_PRESETS),
    ).ask()
    if not provider:
        return None
    models = [model.id for model in get_models(provider)]
    model = questionary.select("Choose a model:", choices=models).ask()
    if not model:
        return None

    key = ""
    if PROVIDER_TRANSPORTS.get(provider) != "chatgpt":
        key = questionary.password("API key:").ask() or ""

    target = config_path or (Path.cwd() / CONFIG_FILENAME)
    settings = SettingsService(target.parent, target)
    changes = {"provider": provider, "model": model}
    settings.save_many(changes)
    if key:
        AuthenticationService().save_api_key(provider, key)
    return target
