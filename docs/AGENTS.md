# Documentation guide

The published Cade Mini docs live in `docs/cade-mini/`. The site is generated
from Markdown and `docs.json`; do not edit `docs/index.html` by hand.

## Rules

- Write documentation in English. Code comments and docstrings are Chinese.
- Keep one source of truth. Link to the design and schema instead of copying
  invariants into several pages.
- Add every new page to a section in `docs.json`. The only exceptions are paths
  matched by `exclude` (decision records and the postmortem template).
- Use relative links to other docs and code. Do not use absolute filesystem
  paths.
- Use fenced code blocks with a language. Keep commands copy-pasteable.
- State limitations directly. Do not describe intended behavior as shipped.

## Document types

- Start: tutorials with a first successful task.
- Concepts and Design: invariants, seams, event flow, and cross-cutting
  contracts.
- Subsystems: one reference page per implementation area; types and behavior,
  not user procedures.
- Safety and Extensions: user-facing behavior of policy and add-ons.
- Cookbook: numbered procedures with a verification step.
- Operations: hosts, development, testing, and parity.
- History: postmortems, persistence policy, and upgrade notes. Chronology
  belongs only in postmortems.
- Decisions: durable choices that change a public contract.

Generated pages (`design/config-catalog.md`, `design/event-catalog.md`, and
`design/module-graph.md`) are written by the docs tool. Do not edit them by
hand.

## Checks

```bash
uv run --locked python -m cade_mini.docs check
uv run --locked --extra docs python -m cade_mini.docs build
uv run --locked python -m cade_mini.docs llms --write
uv run --locked python -m cade_mini.docs catalogs --write
```

`check` validates the manifest, page existence, relative links, anchors,
`llms.txt`, `llms-full.txt`, and the generated catalogs. It runs in CI. Run it
before committing docs changes.

## Page layout

Start with a level-one title, then a short statement of the task or concept.
Use level-two sections for procedures and reference groups. Put the most common
path first. Link related guides at the end.

## Decision records

Use `docs/cade-mini/decisions/TEMPLATE.md` for choices that change durable state
or public interfaces. Number them sequentially, set a status, and link the code
and tests that enforce the decision.
