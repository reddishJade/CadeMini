# Review only

Use `plan` and `--read-only` when the task is to inspect, not change.

## Procedure

```bash
cade --mode plan --read-only "Review the request builder for evidence loss. Cite files and lines; do not modify anything."
```

Plan mode hides write and edit tools and denies mutating shell commands.
`--read-only` also denies memory, MCP, and any remaining mutation.

## Verify

- `git status` is clean.
- The answer cites files by relative path and line.
- Claims about tests name the command and result.
- Findings distinguish a bug from a preference.

## Keep

For a review, keep the report in the conversation or a review document. Do not
create a working note unless the review spans multiple sessions.

## Limits

The model can still read anything inside the workspace. A review of untrusted
code still needs human judgment; the policy prevents writes, not misleading
prose.
