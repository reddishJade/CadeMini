# 0008: Use Sign in with ChatGPT for ChatGPT plan usage

Status: accepted

## Context

Cade's legacy `openai-codex` provider used the legacy Codex OAuth flow. It
relied on a fixed Codex client ID, a device-code endpoint, and the
`chatgpt.com/backend-api` transport. OpenAI now documents Sign in with ChatGPT
(SIWC) for open-source apps that use a user's ChatGPT plan. SIWC uses dynamic
client registration, a loopback callback, and the public Responses API at
`https://api.openai.com/v1`. The legacy backend, its headers, and its
device-code flow are not part of the SIWC contract.

Alternatives were to keep the legacy flow alongside SIWC or to keep the old
`openai-codex` name as a compatibility alias. Keeping both leaves an unsupported
backend and duplicate naming. An alias would preserve a name that no longer
describes the implementation.

## Decision

The provider ID and transport are `chatgpt`. The legacy `openai-codex` provider
ID, `openai_codex` transport, class names, model aliases, and credential fields
are removed without a compatibility alias or migration. The login
implementation follows SIWC:

- The first login authorizes `client_id=dynamic_agent_client` with
  `agent_name_hint` and a stable `ext_agent_host_id`. The callback issues a
  client ID, which is stored in `auth.json`.
- Later logins reuse the issued client ID and send `id_token_hint` and
  `login_hint`.
- Authorization uses PKCE S256, `state`, `nonce`, the scopes
  `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`,
  and `resource=https://api.openai.com/v1`.
- The callback listens on `127.0.0.1` at `/auth/callback`. The port defaults to
  1455 and may fall back to an available port.
- The ID token is verified against OpenAI's JWKS, including issuer, audience,
  expiry, and nonce.
- The credential stores the issued client ID, ID token, subject, email, scopes,
  and issuer in `extra`. The host ID is not part of the credential because it
  identifies the runtime, not the ChatGPT account.
- Refresh sends `grant_type=refresh_token`, the issued client ID, the refresh
  token, and `resource`. It omits `scope`.
- Inference uses `https://api.openai.com/v1/responses` with `store=false` and
  `stream=true`. Requests omit fields that SIWC rejects, including
  `max_output_tokens`, `temperature`, `top_p`, and `metadata`.
- Function tools are sent in an `additional_tools` input item.
- Device-code login is removed because SIWC does not define it.

The stable host ID lives in `~/.cade/host.json`, separate from
`~/.cade/auth.json`.

## Consequences

- Configurations that use `openai-codex` must change to `chatgpt`. Saved
  `openai-codex` credentials are ignored, so run `cade login` again.
- Headless hosts cannot use a device code. Complete login locally, copy
  `auth.json` to the remote host, and let that host create its own host ID on the
  next login. Or use an OpenAI API key.
- The ChatGPT backend, Codex protocol headers, and Codex-specific request
  rewriting are removed.
- `tests/test_siwc_auth.py`, `tests/test_auth.py`,
  `tests/test_provider.py`, and `tests/test_responses_siwc_contract.py`
  enforce this contract.
- `pyjwt[crypto]` is a dependency because ID token signature validation is
  required.

The login surface is documented in [CLI](../cli.md) and
[Quickstart](../quickstart.md). The provider boundary remains in
[0007](0007-responses-only-provider.md).
