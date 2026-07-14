# Claude Code plugin

This repo doubles as a single-plugin Claude Code marketplace.

| File | What it is |
|---|---|
| `plugin.json` | The plugin manifest. Points at `assets/claude/skills/`, the only thing the plugin ships. |
| `marketplace.json` | The marketplace manifest. One entry — this plugin, sourced from the repo root. |

## Install

```
/plugin marketplace add dosatos/minimise
/plugin install minimise@minimise
```

## The five skills

All five are **explicit-only** (`disable-model-invocation: true`). Nothing fires on its
own — you type the command.

| Command | What it does |
|---|---|
| `/minimise:job` | Runs multi-step work as a background job: authors the plan YAML, wires the gates, runs it, reports what landed. |
| `/minimise:loop` | Runs open-ended iteration on one artifact: plan → implement → evaluate, until the goal is met or `max_iterations` is hit. |
| `/minimise:review-plan` | The **blocking** plan gate. Reads a plan or loop spec on stdin, prints `REVIEW: PASS` / `REVIEW: FAIL`. |
| `/minimise:review-implementation` | The **advisory** post-task reviewer. Reads the plan on stdin, inspects the diff, reports findings; never aborts the job. |
| `/minimise:setup` | Installs and verifies the prerequisites: the `mini` CLI, the `claude` CLI, a git repo. |

The two reviewers are meant to be driven from a plan's hooks, not just typed. See the
README's hooks section for the `shell:` strings.

## Not shipped by the plugin

`/onboard`, `/handoff`, and `/humanize` live in `.claude/commands/`. They are this repo's
own maintainer workflow, not part of the product, and the plugin does not install them —
`plugin.json` declares no `commands` key. Copy them by hand if you want them:

```bash
cp .claude/commands/* ~/.claude/commands/
```

## Versioning

The version lives in three places and they must all agree:

- `.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `pyproject.toml`

Bump them together. `tests/test_plugin_manifest.py` fails if they drift.
