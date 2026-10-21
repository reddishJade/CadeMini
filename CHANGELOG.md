# Changelog

## Unreleased

- Renamed the distribution, Python package, command, configuration file, and
  state directories from `cade-mini` to `cade`. The tool does not read
  `~/.cade-mini/`, project `.cade-mini/`, or `cade-mini.json`, and does not
  migrate them. See the [upgrade guide](docs/upgrade/README.md).
- Removed the local web host and the `--web`, `--host`, and `--port` options.
- Made Cade an independent `cade` distribution and `cade` Python package, with
  its own provider adapters and process execution code.
- Removed the earlier Cade project's runtime, command, tests, and documentation
  from this repository. No compatibility layer is provided.
- Added `cade login` for Sign in with ChatGPT browser OAuth and provider API keys.
- Replaced the legacy Codex OAuth flow with Sign in with ChatGPT (SIWC). The
  login now uses dynamic client registration, a stable host ID, ID token
  validation, and the public Responses API instead of the ChatGPT backend.
  Device-code login is removed because SIWC does not define it.
- Renamed the ChatGPT plan provider from `openai-codex` to `chatgpt` and the
  transport from `openai_codex` to `chatgpt`. No alias or credential migration
  is kept. Update configurations and run `cade login` again.
- Reduced the AI layer to the OpenAI Responses protocol. The DeepSeek, ChatGLM,
  MiMo, and OpenAI Chat Completions adapters are removed. Use `chatgpt` for
  ChatGPT plan usage or `openai` with an API key.
- Moved credentials and skill discovery to `.cade` directories.
- Updated development checks, documentation, CI, and packaging for Cade only.
