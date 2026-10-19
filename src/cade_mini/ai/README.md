# AI protocols and streaming events

The AI layer translates the OpenAI Responses protocol and normalizes streaming
chunks, reasoning, and tool calls into a typed `ProviderEvent` stream for the
agent. Cade Mini supports only Responses: an API key against `api.openai.com`,
or ChatGPT Codex OAuth against the ChatGPT backend.

## Data flow

```text
Model request: messages and tools
  → HTTP POST / SSE
  → Responses adapter
  → Stream decoding and normalization
  → ReasoningDelta / TextDelta / ToolCallEvent / FinalMessage
  → UsageUpdate
```

### Contracts

[events.py](events.py) defines:

- `ReasoningDelta`: reasoning text increments.
- `TextDelta`: answer text increments.
- `ToolCallEvent`: tool names and JSON arguments.
- `FinalMessage`: termination with reasons such as `end_turn`, `tool_use`, or `max_tokens`.
- `ProviderFailure`: structured network or HTTP failures.

[types.py](types.py) defines `ProviderConfig`, including `api_key`, `model`, `base_url`, `thinking`, and `reasoning_effort`. [models.py](models.py) resolves aliases and window sizes; [cache.py](cache.py) tracks prompt cache statistics.

## Invariants

AI owns communication, parameter encoding, and stream decoding. It must not depend on Agent steps, rollover, session persistence, or OS tools.

Normalize reasoning fields to `ReasoningDelta` for presentation. Parsing failures and timeouts must produce an explicit failure or exception, not silently drop content.

## Example

Set `OPENAI_API_KEY` before running this example. `messages` and `tools` are the request's message and tool-definition lists. Consume the stream inside an async function.

```python
import os

from cade_mini.ai.events import FinalMessage, ReasoningDelta, TextDelta, ToolCallEvent
from cade_mini.ai.providers.registry import ModelProfileConfig, build_model_provider

# 根据模型配置构建 Responses provider
provider = build_model_provider(
    ModelProfileConfig(
        provider="openai",
        transport="openai_responses",
        chat_model="gpt-5.5",
        api_key=os.environ["OPENAI_API_KEY"],
    )
)

# 消费归一化事件
async for event in provider.stream(messages, tools=tools):
    match event:
        case ReasoningDelta(chunk=thought):
            print(f"[Thinking] {thought}", end="")
        case TextDelta(chunk=text):
            print(text, end="")
        case ToolCallEvent(calls=calls):
            print(f"[Tool Call] {calls}")
        case FinalMessage(stop_reason=reason):
            print(f"\n[Finished: {reason}]")
```

## Submodules

- [providers/](providers/README.md): Responses adapters and shared encoding helpers.
- [auth/](auth/README.md): authentication contracts, Codex OAuth and refresh, and pre-request credential resolution. Hosts implement `CredentialStore`; AI does not know authentication file paths or CLI interactions.
