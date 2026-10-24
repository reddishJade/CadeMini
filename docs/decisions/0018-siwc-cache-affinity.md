# 0018: Keep SIWC cache affinity stable

Status: accepted

## Context

SIWC uses the public Responses endpoint and stateless history replay. Stable
history alone did not produce reliable cache hits on the selected model.
OpenAI's [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
documents `prompt_cache_key`, but does not specify session headers as a public
cache requirement. Its official
[Responses client](https://github.com/openai/codex/blob/89461ea6833832b0551ce24ee32205824d5c108d/codex-rs/codex-api/src/endpoint/responses.rs)
also sends stable `session-id` and `thread-id` headers. Live requests verified
that this association improves cache reuse on the SIWC endpoint.

## Decision

The ChatGPT adapter sends a stable provider-local ID in those two headers and
`prompt_cache_key`. `StreamOptions.session_id` can select a different group.
Long IDs use SHA-256 to fit the cache key length limit. Other request content
and headers remain unchanged. Ordinary OpenAI and DeepSeek transports are not
changed.

This is an implementation-dependent optimization, not a public guarantee of
cache hits. Keep SIWC's public endpoint, OAuth, `store=false`, `stream=true`,
`additional_tools`, and complete history replay from
[decision 0008](0008-siwc-login.md). Do not restore the legacy backend, Codex
protocol-version headers, or internal Responses Lite flags.

## Consequences

The [adapter](../../src/cade/ai/providers/responses.py) owns request affinity;
no product state or credentials enter the cache key. The
[contract tests](../../tests/test_responses_siwc_contract.py) cover stable defaults,
explicit overrides, matching headers, and isolation from other transports.
Cache performance must still be checked against the actual selected endpoint
and model. Do not infer support for newer cache controls from a model name.
