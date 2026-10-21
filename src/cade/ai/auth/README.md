# Provider authentication

This module owns authentication contracts and provider-specific OAuth login,
token exchange, and refresh. It does not choose credential file paths or import
the application or hosts.

- `types.py`: credentials, auth providers, and the `CredentialStore` protocol.
- `chatgpt.py`: Sign in with ChatGPT browser PKCE, client registration, ID
  token validation, and token refresh.
- `registry.py`: provider IDs and authentication implementations.
- `resolver.py`: credential expiration and refresh through the store protocol.

`cade.app.auth.JsonCredentialStore` owns `~/.cade/auth.json` and
`~/.cade/host.json`. `cade.hosts.auth` owns the `cade login`
interaction. The provider assembly in `cade.app.provider` resolves
credentials before building a model adapter.
