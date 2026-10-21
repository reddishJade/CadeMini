# Subagents

## Child sessions

The `subagent` tool runs one child task in its own session and returns the final
answer with the child session ID:

```json
{"prompt": "Find the failing test and report the file and line."}
```

The child:

- gets the parent's provider, budget, policy, approver, and request context;
- gets the base tools plus a history tool bound to its own log;
- does not get another `subagent` tool, so it cannot recurse;
- uses at most 20 steps;
- writes its own session under `<sessions>/subagents/`.

The parent records only the tool call and the returned summary. The child log
keeps the detailed evidence.

`delegate` uses the same runner for one or more tasks:

```json
{"tasks": ["Inspect the parser", "Add a regression test"], "mode": "build"}
```

Tasks run sequentially in separate child sessions. Each result includes the
child session ID; the parent gets one combined tool result. Child modes cannot
expand parent permissions: a Plan parent cannot delegate Build work.

## Limits

Subagents are sequential and share the parent's approval context rather than
running in parallel.
