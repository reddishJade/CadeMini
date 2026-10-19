# 0007: The AI layer supports only the OpenAI Responses protocol

Status: accepted

## Context

The `ai/` layer carried adapters for OpenAI Chat Completions, DeepSeek, ChatGLM,
and MiMo, plus a compatibility base, a retry runtime, and stream helpers. Each
adapter added provider-specific configuration, model metadata, cost logic, and
test surface. Only the OpenAI Responses adapters and Codex OAuth were in use.
The core loop already owns retries, so the provider runtime duplicated that
behavior.

Alternatives were to keep the extra adapters behind the same registry, or to
keep the compatibility base for future providers. Both keep dead code, extra
dependencies, and provider-specific fields in generic configuration.

## Decision

Cade Mini supports only the OpenAI Responses protocol. Two transports remain:

- `openai_responses` uses an API key from `OPENAI_API_KEY` or
  `cade-mini login --provider openai`.
- `openai_codex` uses ChatGPT OAuth from `cade-mini login`.

`build_model_provider` in `ai/providers/registry.py` constructs exactly these
two adapters. The model registry contains OpenAI models only. The
`tenacity` and `python-dotenv` dependencies are removed because the remaining
adapter uses neither.

## Consequences

- Provider-specific parameters stay in `responses.py`; generic configuration
  carries no vendor fields.
- Adding a provider requires a new adapter and a deliberate change to this
  record, not a new entry in a long registry.
- Existing DeepSeek, ChatGLM, MiMo, and OpenAI Chat Completions configurations
  are rejected or resolve to Responses behavior. Users must select
  `openai-codex` or `openai`.
- `tests/test_mini_provider.py`, `tests/test_mini_auth.py`,
  `tests/test_mini_config.py`, and `tests/test_responses_codex_cache_contract.py`
  enforce the transport and login surface.

The invariant is documented in [Architecture](../architecture.md) and the
[provider adapters](../../src/cade_mini/ai/providers/README.md).
