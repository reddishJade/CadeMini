"""TUI 模型与推理等级选择器的展示数据。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cade.ai.models import get_model_reasoning_efforts, get_models

if TYPE_CHECKING:
    from .bridge import CadeInteractionApp


@dataclass(frozen=True)
class AvailableModelEntry:
    model: str
    transport: str
    provider: str
    source_label: str


def reasoning_effort_levels_for_transport(
    transport: str, model: str
) -> tuple[str, ...]:
    """返回模型声明支持的 effort，不替未知模型猜测能力。"""
    del transport
    return get_model_reasoning_efforts(model)


def get_available_model_entries(app: CadeInteractionApp) -> list[AvailableModelEntry]:
    """只列出已登录或已配置 API key 的提供方模型。"""
    transports = {
        "chatgpt": "chatgpt",
        "openai": "openai_responses",
        "deepseek": "deepseek_responses",
    }
    providers = app.service.settings.configured_providers(app.service.auth.store)
    return [
        AvailableModelEntry(model.id, transports[provider], provider, provider)
        for provider in providers
        for model in get_models(provider)
    ]


def is_current_model_entry(
    entry: AvailableModelEntry, model: str, transport: str
) -> bool:
    return entry.model == model and entry.transport == transport


def current_effort_options(app: CadeInteractionApp) -> tuple[str, ...]:
    return get_model_reasoning_efforts(app.service.provider.model)


def current_model_options(app: CadeInteractionApp) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(entry.model for entry in get_available_model_entries(app))
    )


def format_model_entry(entry: AvailableModelEntry) -> str:
    return f"{entry.model:20} {entry.source_label}"
