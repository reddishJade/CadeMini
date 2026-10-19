# Provider authentication

This module owns authentication contracts and provider-specific OAuth login,
token exchange, and refresh. It does not choose credential file paths or import
the application or hosts.

- `types.py`: credentials, auth providers, and the `CredentialStore` protocol.
- `chatgpt.py`: Sign in with ChatGPT browser PKCE, client registration, ID
  token validation, and token refresh.
- `registry.py`: provider IDs and authentication implementations.
- `resolver.py`: credential expiration and refresh through the store protocol.

`cade_mini.app.auth.JsonCredentialStore` owns `~/.cade-mini/auth.json` and
`~/.cade-mini/host.json`. `cade_mini.hosts.auth` owns the `cade-mini login`
interaction. The provider assembly in `cade_mini.app.provider` resolves
credentials before building a model adapter.
