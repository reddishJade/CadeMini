# Fix a bug

Use `build` mode for the change. Keep the patch minimal and verify with the
repository's tests.

## Procedure

1. Reproduce the failure:

   ```bash
   cade --mode plan -p "Read the failing test and explain the failure."
   ```

2. Fix it:

   ```bash
   cade --mode build --yes -p "Fix the failing test with the smallest change, then run pytest."
   ```

3. Inspect the diff and the snapshot:

   ```bash
   git diff
   cade --undo  # only if the change is wrong
   ```

## Verify

- The targeted test passes.
- The full relevant test file passes.
- The diff contains only the intended change.
- The assistant names the command it ran.

## Keep

The run log records the request, tool calls, and test output. The snapshot
allows recovery before the next run. For a reviewable change, keep the diff in
the commit, not in a docs file.

## Avoid

- `--read-only` when you intend to edit.
- `--yes` on a shared repository without rules.
- A broad refactor when a one-line condition fixes the test.
