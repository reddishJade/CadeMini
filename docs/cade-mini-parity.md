# Cade Mini parity acceptance

Date: 2026-10-18. Host: Windows. Provider: DeepSeek `deepseek-chat` in both
agents. Fixtures and session evidence are under the ignored `e2e-results/`
directory.

## Workflows

Two clean Git repositories were prepared from the same baseline and given the
same `AGENTS.md` instruction: "Run pytest after changes. Keep changes minimal."

| Workflow | Cade Mini | Cade |
| --- | --- | --- |
| Fix `add` bug, run pytest | 5 steps, answer `FIXED`, `calc.py` changed `a - b` to `a + b`, 1 passed | 4 steps, answer `FIXED`, identical `calc.py` change, 1 passed |
| Add `mul` and a test, run pytest | 5 steps, answer `PASS`, added `mul` and `test_mul`, 2 passed | 4 steps, answer `PASS`, identical diff, 2 passed |
| Resume and explain the bug | 1 step, answered `calc.py` and operator `+` | 1 step, same answer |

The first workflow ran Mini with `--mode build --yes`. Cade ran with `--mode
build --approval auto-review`; its automatic reviewer approved the `pytest`
command. The second workflow used the same settings.

## Observations

- Both agents read before editing, changed only the necessary files, ran
  `pytest`, and reported the verified result.
- Both produced the same source changes for the bug fix and the feature.
- Both resumed their prior session and recalled the earlier bug.
- Cade emits a JSONL event stream and additional budget/approval telemetry;
  Mini emits human-readable tool progress and records request/run facts in its
  session log.
- Mini used one more model step because it also issued a `glob` inspection.
  This did not change the result.

## Remaining differences

- Mini's file snapshots cover `write`, `edit`, and common single-command shell
  mutations; complex pipelines are not snapshotted.
- Mini keeps one persistent MCP connection per server; Cade's MCP lifecycle and
  capability surface are still broader.
- The OS sandbox is available only on Linux; on Windows Mini relies on path
  boundaries, modes, shell classification, and approvals.
- The live Codex acceptance remains pending because the account returned HTTP
  429.

These differences are recorded in the implementation status; they do not affect
the workflows above.
