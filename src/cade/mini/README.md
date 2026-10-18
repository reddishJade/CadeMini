# Cade Mini package

Cade Mini is a parallel implementation inside the `cade-agent` distribution. It
shares only `cade.ai` (providers, auth, models) and
`cade.harness.execution_env` (subprocess and sandbox) with Cade. Everything else
here is independent.

## Entry points

```bash
cade-mini
python -m cade.mini
cade-mini --web --port 8765
```

`cade.mini.cli:main` is the console script target. Mini is not a separate wheel;
installing `cade-agent` installs both `cade` and `cade-mini`.

## Module groups

| Group | Modules |
| --- | --- |
| Core pipeline | `types`, `log`, `projection`, `request`, `loop`, `compaction`, `window` |
| Tools | `tools`, `history` |
| Safety | `policy`, `shell`, `grants`, `approval`, `review`, `sandbox` |
| Extensions | `skills`, `memory`, `hooks`, `mcp`, `subagents`, `snapshots`, `goal` |
| Hosts and docs | `cli`, `web`, `docs` |
| App plumbing | `config`, `provider`, `auth`, `prompts`, `instructions`, `note` |

The directory is flat. Read [the code map](../../../docs/cade-mini/design/code-map.md)
for the layer view and [the comparison with
Cade](../../../docs/cade-mini/design/cade-comparison.md) for the capability gap.
