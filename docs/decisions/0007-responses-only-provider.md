# 0007: The AI layer supports only the Responses protocol

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

Cade supports only the Responses protocol. Three transports remain:

- `openai_responses` uses an API key from `OPENAI_API_KEY` or
  `cade login --provider openai`.
- `chatgpt` uses Sign in with ChatGPT (SIWC) from `cade login`.
- `deepseek_responses` uses an API key from `DEEPSEEK_API_KEY` or
  `cade login --provider deepseek`.

`build_model_provider` in `ai/providers/registry.py` constructs exactly these
three adapters. DeepSeek reuses the Responses decoder but has its own endpoint,
credential, transport name, and model metadata. The `tenacity` and
`python-dotenv` dependencies remain removed because no adapter uses them.

The SIWC login contract is defined in
[0008](0008-siwc-login.md). All transports call a public Responses API;
`chatgpt` differs in its OAuth credential and request constraints, and
`deepseek_responses` differs in its endpoint and stateless request shape.

## Consequences

- Provider-specific parameters stay in `responses.py`; generic configuration
  carries no vendor fields.
- Adding a provider requires a new adapter and a deliberate change to this
  record, not a new entry in a long registry.
- Existing ChatGLM, MiMo, and OpenAI Chat Completions configurations are
  rejected or resolve to Responses behavior. Users must select `chatgpt`,
  `openai`, or `deepseek`.
- `tests/test_provider.py`, `tests/test_auth.py`,
  `tests/test_config.py`, and `tests/test_responses_siwc_contract.py`
  enforce the transport and login surface.

The invariant is documented in [Architecture](../architecture.md) and the
[provider adapters](../../src/cade/ai/providers/README.md).
