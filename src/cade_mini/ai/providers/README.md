# Provider adapters

Providers translate Responses request parameters, endpoints, and SSE streams
into the common provider protocol and `ProviderEvent` stream. Cade Mini supports
only the Responses protocol.

## Assembly

```text
ModelProfileConfig
  → build_model_provider()
  → OpenAIResponsesProvider / ChatGPTResponsesProvider / DeepSeekResponsesProvider
  → Responses requests
```

### Components

- [base.py](base.py): model interaction protocols.
- [responses.py](responses.py): Responses API for OpenAI API keys, SIWC plan usage, and DeepSeek API keys.
- [_codec.py](_codec.py): strict schema conversion and tool argument decoding.
- [registry.py](registry.py): `PROVIDER_REGISTRY` constructor mapping and `build_model_provider`.

## Invariants

Keep transport-specific parameters inside adapters, passing them through `extra` rather than adding vendor fields to generic configuration. Providers own client configuration and model metadata, not product state. Upper-layer tests should use protocol-compatible streams; do not retain obsolete test stubs in production packages.

## Example

Set `OPENAI_API_KEY`. Pass message and tool-definition lists as `messages` and
`tool_specs`, then consume `events` inside an async function.

```python
import os

from cade_mini.ai.providers.registry import ModelProfileConfig, build_model_provider

# 装配 Responses provider
provider = build_model_provider(
    ModelProfileConfig(
        provider="openai",
        transport="openai_responses",
        chat_model="gpt-5.5",
        api_key=os.environ["OPENAI_API_KEY"],
    )
)

# 获取事件流
events = provider.stream(messages, tools=tool_specs)
```
