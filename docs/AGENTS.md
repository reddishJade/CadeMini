# Documentation guide

The published Cade docs live in `docs/`. The site is generated
from Markdown and `docs.json`; do not edit `docs/index.html` by hand.

## Rules

- Write primary documentation in English and the `docs/zh/` mirror in Simplified Chinese. Preserve each document's language when editing or syncing it. Never replace Chinese sections with English prose or English summaries. Keep identifiers, protocol fields, paths, and commands unchanged. Code comments and docstrings are Chinese.
- `docs/zh/` is the Chinese mirror of current pages. It has its own
  `docs/zh/docs.json` and uses the same relative file names. History records
  (`history/`, `postmortem/`, `persistence/`, `upgrade/`) stay English; Chinese
  pages link to them with `../` or `../../` paths.
- Keep one source of truth. Link to the design and schema instead of copying
  invariants into several pages.
- Add every new page to a section in `docs.json`. The only exceptions are paths
  matched by `exclude` (this file, decision records, the postmortem template,
  and `zh/`). Keep `docs/zh/docs.json` in sync when a current page is added,
  renamed, or removed.
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
- Reference: persistent format contracts under `reference/`.
- History: pre-split design, harvest, and acceptance records under `history/`.
  Postmortems, persistence policy, and upgrade notes have their own directories.
- Decisions: durable choices that change a public contract.

Generated pages (`design/config-catalog.md`, `design/event-catalog.md`, and
`design/module-graph.md`) are written by the docs tool. Do not edit them by
hand.

## Checks

```bash
uv run --locked python -m cade.docs check
uv run --locked --extra docs python -m cade.docs build
uv run --locked python -m cade.docs llms --write
uv run --locked python -m cade.docs catalogs --write
uv run --locked python -m cade.docs --root docs/zh llms --write
uv run --locked python -m cade.docs --root docs/zh build --out docs/zh/index.html
```

`check` validates the manifest, page existence, relative links, anchors,
`llms.txt`, `llms-full.txt`, and the generated catalogs. It also checks every
direct child directory that has its own `docs.json`, so one run covers `docs/zh/`.
It runs in CI. Run it before committing docs changes.

## Page layout

Start with a level-one title, then a short statement of the task or concept.
Use level-two sections for procedures and reference groups. Put the most common
path first. Link related guides at the end.

## Decision records

Use `docs/decisions/TEMPLATE.md` for choices that change durable state
or public interfaces. Number them sequentially, set a status, and link the code
and tests that enforce the decision.
