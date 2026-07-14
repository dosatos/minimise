# Claude Code plugin

This repo doubles as a single-plugin Claude Code marketplace.

| File | What it is |
|---|---|
| `plugin.json` | The plugin manifest. Points at `assets/claude/skills/`, which ships `plan-review`, `implementation-review`, `setup`, `delegate`, and `refine`. |
| `marketplace.json` | The marketplace manifest. One entry — this plugin, sourced from the repo root. |

## Install

```
/plugin marketplace add dosatos/minimise
/plugin install minimise@minimise
```

Then `/minimise:plan-review`, `/minimise:implementation-review`, `/minimise:setup`,
`/minimise:delegate`, and `/minimise:refine` are available.
See the README's hooks section for wiring the reviewers into a plan.

## Not shipped by the plugin

`assets/claude/commands/` (`/onboard`, `/handoff`, `/humanize`) are repo-workflow
commands, not plugin components. Copy them by hand if you want them:

```bash
cp assets/claude/commands/* ~/.claude/commands/
```

## Versioning

The version lives in three places and they must all agree:

- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `pyproject.toml`

Bump them together. `tests/test_plugin_manifest.py` fails if they drift.
