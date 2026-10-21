# Validation guidelines

Choose checks for the behavior and risk being changed. Keep ordinary test
workspaces and temporary files under `tmp/`, regression reports under
`test-results/`, and end-to-end test results under `e2e-results/`. Keep tool
caches in their dedicated directories, such as `.uv-cache/` and `.pytest_cache/`.
Do not copy account credentials into test evidence.

## Required checks

After code changes, run:

```bash
mkdir -p tmp test-results
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked pytest -q tests --basetemp=tmp/pytest
```

After import, package, or entry-point changes, also check:

```bash
uv run --locked cade --help
uv run --locked python -m cade --help
uv run --locked cade login --help
uv build --no-sources
```

For distribution changes, inspect the wheel and install it in a clean virtual
environment. Check startup outside the source tree and confirm that no `cade`
package, command, or dependency is required.

## Select behavioral checks

- Local computation or policy: use focused regression tests with independently
  checked expectations and edge cases.
- Connected components: test the real components together, controlling only
  external boundaries where needed.
- Provider parsing: use captured, sourced protocol payloads through the adapter.
  Replacing a whole provider tests the application, not the remote protocol.
- Login: test host routing, credential persistence, and failure behavior without
  contacting a live account. Real OAuth acceptance requires explicit approval.
- Real hosts or services: run E2E only when lower-level checks leave a concrete
  workflow risk uncovered. Record the task, prerequisites, commands, expected
  observable results, and independent verification before running it.
- Documentation: check facts, links, commands, generated catalogs, and the diff.

Add reusable tests only for a concrete coverage gap. Tests should protect user
outcomes or explicit contracts, not private implementation details. Do not
change production APIs merely to accommodate tests.

## Report results

State what each check establishes. Separate failures caused by code from missing
permissions, environment prerequisites, or external services. Report checks not
run and remaining risks explicitly. Provider completion text alone is not proof
that a workflow succeeded.
