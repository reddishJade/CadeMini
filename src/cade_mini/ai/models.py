"""模型注册表与模型选择语法解析。"""

from __future__ import annotations

from dataclasses import dataclass

from cade_mini.ai.types import Cost, Model

THINKING_LEVELS = frozenset(
    ("off", "none", "minimal", "low", "medium", "high", "xhigh", "max")
)
GPT_6_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
GPT_6_ASTRA_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
GPT_56_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
GPT_55_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
DEEPSEEK_REASONING_EFFORTS = ("none", "low", "high", "max")


@dataclass(frozen=True)
class ModelMode:
    """解析后的模型选择。"""

    model: str
    provider: str | None = None
    thinking_level: str | None = None


# ── 模型注册表 ──


def normalize_model_id(model_id: str) -> str:
    """规范化模型 ID：去除首尾空白。"""
    return model_id.strip()


_MODELS: dict[str, dict[str, Model]] = {
    "openai": {
        "gpt-6-astra": Model(
            id="gpt-6-astra",
            name="GPT-6 Astra",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=10, output=50, cache_read=1.00),
            reasoning_efforts=GPT_6_ASTRA_REASONING_EFFORTS,
        ),
        "gpt-6-sol": Model(
            id="gpt-6-sol",
            name="GPT-6 Sol",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=2, output=10, cache_read=0.20),
            reasoning_efforts=GPT_6_REASONING_EFFORTS,
        ),
        "gpt-6.1-sol": Model(
            id="gpt-6.1-sol",
            name="GPT-6.1 Sol",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=2, output=10, cache_read=0.10),
            reasoning_efforts=GPT_6_REASONING_EFFORTS,
        ),
        "gpt-6-luna": Model(
            id="gpt-6-luna",
            name="GPT-6 Luna",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=0.10, output=0.50, cache_read=0.01),
            reasoning_efforts=GPT_6_REASONING_EFFORTS,
        ),
        "gpt-5.6-sol": Model(
            id="gpt-5.6-sol",
            name="GPT-5.6 Sol",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=4, output=20, cache_read=0.40),
            reasoning_efforts=GPT_56_REASONING_EFFORTS,
        ),
        "gpt-5.6-terra": Model(
            id="gpt-5.6-terra",
            name="GPT-5.6 Terra",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=2, output=12, cache_read=0.20),
            reasoning_efforts=GPT_56_REASONING_EFFORTS,
        ),
        "gpt-5.6-luna": Model(
            id="gpt-5.6-luna",
            name="GPT-5.6 Luna",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=0.20, output=1.20, cache_read=0.02),
            reasoning_efforts=GPT_56_REASONING_EFFORTS,
        ),
        "gpt-5.5": Model(
            id="gpt-5.5",
            name="GPT-5.5",
            api="openai-responses",
            provider="openai",
            reasoning=True,
            context_window=1_050_000,
            max_tokens=128_000,
            cost=Cost(input=5, output=30, cache_read=0.50),
            reasoning_efforts=GPT_55_REASONING_EFFORTS,
        ),
    },
    "deepseek": {
        # 成本按 DeepSeek 高峰价估算；低谷价为高峰价的一半。
        "deepseek-flash": Model(
            id="deepseek-flash",
            name="DeepSeek V4.1 Flash",
            api="deepseek-responses",
            provider="deepseek",
            reasoning=True,
            context_window=1_000_000,
            max_tokens=384_000,
            cost=Cost(input=0.30, output=1.20, cache_read=0.006),
            reasoning_efforts=DEEPSEEK_REASONING_EFFORTS,
        ),
        "deepseek-v4-pro": Model(
            id="deepseek-v4-pro",
            name="DeepSeek V4 Pro",
            api="deepseek-responses",
            provider="deepseek",
            reasoning=True,
            context_window=1_000_000,
            max_tokens=384_000,
            cost=Cost(input=1.32, output=3.96, cache_read=0.044),
            reasoning_efforts=DEEPSEEK_REASONING_EFFORTS,
        ),
    },
}


def get_providers() -> list[str]:
    return list(_MODELS)


def get_models(provider_name: str) -> list[Model]:
    return list(_MODELS.get(provider_name, {}).values())


def get_model(provider_name: str, model_id: str) -> Model | None:
    return _MODELS.get(provider_name, {}).get(normalize_model_id(model_id))


def get_model_reasoning_efforts(model_id: str) -> tuple[str, ...]:
    """返回已注册模型明确声明的 reasoning effort 能力。"""
    normalized = normalize_model_id(model_id).lower()
    for provider_models in _MODELS.values():
        for registered_id, model in provider_models.items():
            if registered_id.lower() == normalized:
                return model.reasoning_efforts
    return ()


def get_model_cost(model_name: str) -> Cost | None:
    """解析模型单价（美元/百万 token）。"""
    resolved_name = normalize_model_id(model_name)
    for provider_models in _MODELS.values():
        model = provider_models.get(resolved_name)
        if model is not None:
            return model.cost
    return None


def resolve_model(provider_name: str, model_id: str) -> Model:
    """解析模型定义，支持三层回退策略。"""
    model = get_model(provider_name, model_id)
    if model is not None:
        return model
    provider = _MODELS.get(provider_name)
    if provider:
        return next(iter(provider.values()))
    return Model(
        id=model_id, name=model_id, api="openai-responses", provider=provider_name
    )


# ── 模型选择语法解析 ──


def get_model_context_window(model: str | None) -> int | None:
    if not model:
        return None
    model_lower = normalize_model_id(model).lower()
    candidates = (
        (model_id, profile)
        for provider_models in _MODELS.values()
        for model_id, profile in provider_models.items()
    )
    for model_id, profile in sorted(
        candidates,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if model_id in model_lower:
            return profile.context_window
    return None


def parse_model_mode(value: str) -> ModelMode:
    """解析 `provider/model:thinking_level` 模型选择语法。"""
    text = value.strip()
    if not text:
        raise ValueError("model must not be empty")

    provider: str | None = None
    model_part = text
    if "/" in text:
        provider_text, model_part = text.split("/", 1)
        provider = provider_text.strip() or None

    model = model_part
    thinking_level: str | None = None
    if ":" in model_part:
        model, level = model_part.rsplit(":", 1)
        thinking_level = level.strip().lower()
        if thinking_level not in THINKING_LEVELS:
            allowed = "/".join(sorted(THINKING_LEVELS))
            raise ValueError(
                f"invalid thinking level: {thinking_level}. Use {allowed}."
            )

    model = model.strip()
    if not model:
        raise ValueError("model must not be empty")
    return ModelMode(model=model, provider=provider, thinking_level=thinking_level)
