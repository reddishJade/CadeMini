# Run a long session

Long tasks need durable state and a way to recover context. Use `NOTE.md`, a
goal, and the history tool.

## Procedure

1. Set a goal:

   ```bash
   cade-mini --goal "Migrate the request builder to the new budget model" "Start by mapping the current admission rules."
   ```

2. Keep a working note. Ask the model to update `NOTE.md` after each milestone:

   ```text
   Update NOTE.md with completed work, current blockers, and the next action.
   ```

3. Continue the same session:

   ```bash
   cade-mini --continue "Resume from NOTE.md and run the next step."
   ```

4. Compact when the session grows:

   ```text
   /compact
   ```

5. Recover an old detail:

   ```text
   Use history with query "budget" to find the earlier discussion.
   ```

## Verify

- `NOTE.md` names the next action, not just history.
- A resumed run can continue without the original terminal.
- The goal is marked done only when its acceptance check passes.

## Limits

`NOTE.md` is request context and is summarized during compaction. `MEMORY.md` is
for durable facts, not a task log. The session log is the authority for exact
evidence.
