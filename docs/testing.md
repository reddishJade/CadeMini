# Validation guidelines

Choose checks for the behavior and risk being changed. Keep all temporary
workspaces, pytest files, and run evidence inside this repository under
`e2e-results/`. Do not copy account credentials into test evidence.

## Required checks

After code changes, run:

```bash
mkdir -p e2e-results
uv run --locked ruff check src/cade_mini/ tests/
uv run --locked ruff format --check src/cade_mini/ tests/
uv run --locked pyright src/cade_mini/
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
```

After import, package, or entry-point changes, also check:

```bash
uv run --locked cade-mini --help
uv run --locked python -m cade_mini --help
uv run --locked cade-mini login --help
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
