# Subsystems

Each page owns one subsystem's types, behavior, and failure modes. The
[architecture](../architecture.md) is the map; these pages are the reference.

- [Log](log.md): append, lock, branch, list, and repair.
- [Projection and request](projection-request.md): deterministic context and
  budget admission.
- [Loop](loop.md): provider calls, tools, approvals, retries, and facts.
- [Tools and policy](tools-policy.md): workspace tools, modes, rules, grants,
  shell classification, approvals.
- [Context and compaction](context-compaction.md): summaries, windows, history,
  and request context.
- [Extensions](extensions.md): skills, memory, hooks, MCP, subagents, snapshots,
  goals, and hosts.

A subsystem page describes current behavior. Historical decisions belong in
[decision records](../decisions/README.md) and incidents in
[postmortems](../postmortem/README.md).
