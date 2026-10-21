# Add a feature

Write the test first, then implement, then verify. Use `build` mode and keep the
diff reviewable.

## Procedure

1. Ask for a failing test or write it yourself:

   ```bash
   cade --mode build --yes "Add a test for mul() that asserts 2 * 3 == 6; run it and confirm it fails."
   ```

2. Implement the smallest change:

   ```bash
   cade --mode build --yes "Implement mul() and run the new test."
   ```

3. Review:

   ```bash
   git diff
   uv run --locked pytest -q
   ```

## Verify

- The new test fails before the change and passes after it.
- Existing tests still pass.
- The implementation has no unrelated edits.
- Public behavior is documented in the relevant guide.

## Undo

If the change is wrong, use `cade --undo` before starting another attempt.
This restores one recorded file change at a time; it does not rewind the
conversation.

## Keep

Update the guide or decision record when the feature changes a public contract.
Tests belong in `tests/`; docs belong under `docs/`.
