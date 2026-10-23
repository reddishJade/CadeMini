# Changelog

## Unreleased

## 0.1.3

- Removed the redundant `subagent` tool. Use `delegate` for one or more
  child tasks, with optional restrictions on child permissions.
- Added source totals and loaded file estimates to `/context`, OpenAI API
  input counting, and DeepSeek's official text tokenizer. ChatGPT plan
  sessions use local estimates instead of an unsupported counting endpoint.
- Added menu-based configuration presets, custom values, and dedicated
  queue and steer shortcuts in the TUI.
- Simplified workspace tools with exact batched edits, bounded text and image
  reads, native file search, and separate Bash and PowerShell execution.
- Added multiple-choice questions and web retrieval. Web search uses Exa;
  remove the obsolete `websearch_provider` configuration field.
- Improved feedback for incomplete turns and browser login output.

## 0.1.2

- `cade "TASK"` now starts the TUI, sends the task as its first message, and
  keeps the session open. Use `-p TASK`, `--prompt TASK`, or `cade exec TASK`
  for one-shot runs.
- `cade --undo` now undoes one action through checkpoint recovery and accepts
  `--resume SESSION_ID`.
- Session logs and their directories use private permissions on POSIX, and an
  existing readable log is tightened when it is opened.
- Session log reads reuse validated entries behind file metadata checks, and
  snapshot index records append with torn-tail recovery.

## 0.1.1

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
