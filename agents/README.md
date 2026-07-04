# Agent skills & commands

Claude Code skills and slash commands that pair with `mini`, shared here so
anyone using this repo gets them too (the live copies live in the gitignored
`~/.claude/`, so they can't ship from there).

| | What it does |
|---|---|
| `skills/mini-plan-review/` | Blocking `pre_plan` gate: reviews a plan on stdin, prints `REVIEW: PASS/FAIL`. |
| `skills/mini-implementation-review/` | Non-blocking `post_task`/`post_plan` review of the produced diff vs. the plan. |
| `commands/onboard.md` | `/onboard` — read the latest handoff + the delegate-to-`mini-job` execution rule. |
| `commands/handoff.md` | `/handoff` — write a session handoff for next time. |
| `commands/humanize.md` | `/humanize` — cut noise from a document while keeping all content. |

## Install

Copy into your Claude Code config (user-level shown; swap for a project `.claude/`):

```bash
cp -R agents/skills/*   ~/.claude/skills/
cp    agents/commands/* ~/.claude/commands/
```

Then `/onboard`, `/handoff`, `/mini-plan-review`, `/mini-implementation-review`
are available. See the README's hooks section for wiring the reviewers into a plan.
