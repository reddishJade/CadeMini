# Changelog

## Unreleased

- Made Cade Mini an independent `cade-mini` distribution and `cade_mini` Python
  package, with its own provider adapters and process execution code.
- Removed the old Cade runtime, command, tests, and documentation from this
  repository. No compatibility layer is provided.
- Added `cade-mini login` for Codex browser/device-code OAuth and provider API keys.
- Reduced the AI layer to the OpenAI Responses protocol. The DeepSeek, ChatGLM,
  MiMo, and OpenAI Chat Completions adapters are removed. Use `openai-codex` for
  ChatGPT OAuth or `openai` with an API key.
- Moved credentials and skill discovery to `.cade-mini` directories.
- Updated development checks, documentation, CI, and packaging for Mini only.
