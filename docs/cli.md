# CLI

The `cade` entry point runs one task, starts an interactive terminal host,
or manages sessions.

```bash
cade [TASK] [options]
cade -p TASK [options]
cade exec TASK [options]
```

`cade` starts the TUI in an interactive terminal. An optional positional task
is sent as its first message; the TUI stays open afterward. Use `-p TASK`
(or `--prompt TASK`) or `exec TASK` to run one task and exit. The positional
TUI prompt and `-p` cannot be combined.

## Login

```bash
cade login
cade login --provider openai
cade login --provider deepseek
```

`--provider` defaults to `chatgpt`, which uses Sign in with ChatGPT (SIWC)
in a browser. `openai` and `deepseek` use `api_key` with a hidden input prompt.
Credentials are stored in `~/.cade/auth.json`; the stable SIWC host ID is
stored in `~/.cade/host.json`. Login does not change project provider or
model settings. Cade supports only the Responses protocol. ChatGPT login
and OpenAI API keys expose the registered OpenAI chat model pool through separate
authentication routes. A refreshable OAuth token still makes models selectable;
the provider refreshes it when needed.

## One-shot options

- `-p TASK` / `--prompt TASK` runs one task and exits, like `cade exec TASK`.

- `--project-root PATH` sets the workspace. The default is the current
  directory.
- `--config PATH` selects a config file. The default is
  `<project-root>/cade.json`.
- `--provider NAME`, `--model NAME`, and `--effort EFFORT` override provider
  defaults.
- `--mode plan|build|ask` selects the work mode. Default is `build`.
- `--sandbox read-only|workspace-write|danger-full-access` selects filesystem
  scope. Default is `workspace-write`; Windows skips OS confinement.
- `--network` / `--no-network` controls sandbox networking. Default is enabled.
- `--read-only` / `--no-read-only` controls mutation denial. Read-only shell
  commands remain available.
- `--yes` approves asks. Use it only in trusted, non-interactive contexts.
- `--auto-review` / `--no-auto-review` lets the model review ask-level calls.
- `--max-steps N` bounds model steps.
- `--context-window N` overrides the model context window. It is required for
  models that are not in the built-in registry.
- `--json` emits JSONL events for a one-shot task.
- `--steer TEXT` injects guidance at the next model step of a one-shot task;
  repeatable.

## Sessions

- `--list-sessions` prints ID, update time, and title, then exits.
- `--continue` resumes the latest session in the project.
- `--resume` resumes the latest session; `--resume ID` resumes a specific one.
- `--sessions-dir PATH` changes the session directory. A relative path is
  resolved against the project root.
- `--undo` reverses one checkpoint action in the latest session and exits without
  contacting a provider or starting MCP. Use `--resume ID --undo` to select a
  session. It uses the same conversation and file recovery as TUI `/undo`.

`--continue` and `--resume` cannot be combined. Session listings and automatic
continuation skip conversations without a nonempty user message. New interactive
sessions keep setup records in memory until the first user or assistant message;
opening the TUI or running `/new` alone creates no session file. Existing files
are not automatically deleted.

## Goals

- `--goal TEXT` sets the workspace goal before running.

## Interactive commands

The TUI accepts these slash commands and provides native selectors for models,
modes, configuration, and permissions.

- `/new` starts a new session. `/clear` is an alias and keeps saved conversations.
- `/continue` opens the same saved-conversation picker as `/sessions`.
- `/resume` opens the saved-conversation picker; `/resume ID` resumes that session.
- `/sessions` opens the same picker as `/resume`.
- `/tree [ENTRY]` navigates the full history tree within the same session.
  Selecting a user input restores it for editing; other entries continue after
  the selected point. The position survives restart and old branches remain.
- `/fork [N|ENTRY]` copies history before a user turn into a new session and puts
  that input in the editor. It can select inactive branches; `N=1` selects the
  latest recorded input. Files stay unchanged.
- `/clone` copies only the active path to a new session and clears the editor.
- `/undo` reverses one user turn or explicit recovery action. `/redo` reapplies
  one undone action without contacting the model or rerunning tools.
- `/rewind` selects an active-path user prompt and offers conversation, code, or
  both. `/rewind ENTRY conversation|code|both` selects the scope directly, then
  opens the confirmation preview.
- `/revert` selects a turn range and all or one tracked file without trimming
  conversation. `/revert START [END] -- PATH ...` selects multiple files; quote
  paths containing spaces and use forward slashes. END is inclusive; omitting it
  includes the latest action. Conflicting later edits block restoration.
- `/compact [INSTRUCTIONS]` summarizes older turns and applies optional guidance.
- `/model [PROVIDER/MODEL]` switches an exact authenticated model or searches the
  model picker for a partial name.
- `/effort [LEVEL]` chooses a model reasoning effort or opens its picker.
- `/thinking [LEVEL]` selects reasoning effort. Use `Ctrl+T` to fold reasoning
  output without changing effort.
- `/rollover [--force]` starts a new context window; `/new-context` is an alias.
- `/new-context [--force]` starts a new context window using `NOTE.md` as the
  handoff. Write that file first, or use `--force` to reset without it.
- `/mode [MODE]` shows or switches the execution mode.
- `/permissions [list|clear]` lists or clears session grants; without arguments it opens a picker.
- `/goal [TEXT|done|clear]` shows, sets, completes, or clears the goal.
- `/context-window [SIZE|default]` shows or changes the current context window.
- `/context` prints the session ID, provider, budget, and event count.
- `/btw QUESTION` asks an independent side question.
- `/steer TEXT` queues guidance for the next model step.
- `/queue [followup|steer|interrupt|TEXT]` chooses how busy `Enter` behaves, or queues TEXT for the next turn.
- `/stop` interrupts a pending model request, including the wait for its first
  output. Tools stop at their cancellation checks.
- `/help` prints the command list.
- `/auth [status|login|logout]` displays authentication status or routes to login/logout.
- `/login` selects an authentication method and provider. `/logout` removes a
  selected stored credential, not environment variables.
- `/connect` selects a provider and opens its model picker after authentication.
- `/skill [NAME]` activates a skill for the next task.
- `/tool [list]` shows available tools and their descriptions.
- `/config [KEY[=VALUE]]` browses settings or updates a project setting.
- `/memory` shows the workspace memory file path.
- `/exit` or `/quit` exits.

The TUI does not register `/rename` or `/mcp`. Recovery changes tracked files,
not arbitrary shell effects, external services, or the user's Git index.
See [recovery coverage and conflicts](snapshots-goals.md#checkpoint-recovery).
`--undo` cannot be combined with a task; both undo entry points use the same
persistent action history.

## Interactive input

- `@path` attaches a workspace file to the task.
- `$skill` loads a skill body into the task.
- `!command` runs a shell command without contacting the model. Permission
  checks still apply.

```text
Review @src/cade/harness/loop.py and use $review-only
!git status
```

During a task, the TUI keeps reading input:

- `/steer TEXT` appends guidance at the next model step.
- `/queue TEXT` runs TEXT after the current task.
- `/stop` or `Esc` interrupts the task without waiting for the next model
  output. Undelivered queued inputs return to the editor with any current draft.
- `Ctrl+C` clears the editor without interrupting the task. Press it again
  within 500 ms to exit.
- Other text is queued as a follow-up task.

When idle with empty input, press `Esc` twice within 500 ms to open `/rewind`.
Tree navigation, fork, and clone do not restore workspace files.
Inside a picker or form, `Esc` closes that interaction instead. Clearing input
with `Ctrl+C` is separate from cancelling a task with `Esc`.

Steering is applied between steps, not inside a provider stream or a running
tool. See [Steering and queueing](design/steering-queue.md).

## Exit codes

- `0`: the run completed.
- `1`: the run ended without completion, or `--undo` found no session or action
  or could not apply recovery.
- `2`: argument, configuration, or startup error.
- `130`: login was cancelled.

## Examples

```bash
cade --list-sessions
cade --continue -p "Summarize the current state."
cade --undo
cade --mode plan -p "Find the smallest fix for the failing test."
cade --mode build --yes -p "Fix the test and run pytest."
```
