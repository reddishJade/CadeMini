# Parity

Cade Mini was accepted against Cade on two real repository workflows and a
session resume. The full report is [Cade Mini parity](history/parity-acceptance.md).

## Workflows

Two clean Git repositories started from the same baseline and the same
`AGENTS.md` instruction: "Run pytest after changes. Keep changes minimal."

Bug fix:

- Task: fix `add` in `calc.py` so pytest passes.
- Both agents changed `a - b` to `a + b`, ran pytest, got one passing test, and
  answered `FIXED`.

Feature:

- Task: add `mul(a, b)` and a test.
- Both agents produced the same diff, ran pytest, got two passing tests, and
  answered `PASS`.

Resume:

- Both agents resumed their prior session, recalled `calc.py`, and identified
  the corrected `+` operator.

## Evidence

- Mini ran with `--mode build --yes`; Cade ran with `--mode build --approval
  auto-review`.
- Both used DeepSeek `deepseek-chat`.
- Mini used five steps per workflow; Cade used four. Mini issued an extra `glob`
  inspection, which did not change the result.
- Cade emits JSONL telemetry and budget/approval events; Mini emits human tool
  progress and appends request/run facts to its session log.

## Differences

- Mini's snapshots cover write/edit and common single-command shell mutations;
  complex pipelines are not covered.
- Mini's MCP client is persistent per server but has a narrower capability
  surface than Cade's.
- The OS sandbox is not implemented; Mini relies on path boundaries, modes,
  shell classification, and approvals.
- The live Codex acceptance passed: write created `codex.txt`, and a resumed
  Codex session read it back.

The parity report records the concrete fixtures and observed results. Re-run a
workflow by creating a clean fixture, running both agents with the settings
above, and comparing the diff and test result rather than the prose answer.
