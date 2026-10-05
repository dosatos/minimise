# Claude Code Project Settings

## Quick Start

**Global commands (work everywhere):**

```
/onboard              # Read latest handoff + get oriented
/handoff              # Create session handoff for next time
```

Or manually:
```bash
cat worklogs/handoffs/session-latest.md
```

## Scratch & Generated Files — Keep the Repo Root Clean

NEVER write scratch, test, or generated artifacts to the repo root. They pollute
the package. Put them under `worklogs/` (gitignored) instead:

- Throwaway plan YAMLs, sample plans, experiment configs → `worklogs/scratch/`
- Test-run output / captured logs → `worklogs/` (or pipe to `/tmp`)
- Session handoffs → `worklogs/handoffs/`
- Working plan docs → `docs/plans/*.md` (gitignored; only `docs/plans/completed/` is tracked)
- Agent/tool working dirs are local-only and globally ignored

The root must stay limited to real package files (`src/`, `tests/`, `docs/`,
packaging, README). If a tool needs a path, point it at `worklogs/`.

## Environment

- **Python:** 3.9+
- **Package:** minimise (pip install -e .)
- **Entry Point:** `mini` command
- **Tests:** `pytest tests/ -v`
- **Config:** `~/.minimise/` (auto-created)

## Session Handoff

At end of each session:
```bash
cp worklogs/handoffs/HANDOFF_TEMPLATE.md worklogs/handoffs/session-YYYY-MM-DD-HHmm.md
# Fill in: what was done, what's next, how to run, gotchas, current state
ln -sf session-YYYY-MM-DD-HHmm.md worklogs/handoffs/session-latest.md
```

Next session, read the handoff to get context.

## Key Commands

```bash
# Orient yourself
cat worklogs/handoffs/session-latest.md

# Run tests
pytest tests/ -v

# Try the tool
mini job new --plan examples/example-plan.yaml
mini job list

# View docs
cat README.md
cat TESTING.md
```

## Build Through `mini`, Not Inline

Implement features and fixes by running a `mini job` (or a `mini loop` for
open-ended refinement of one artifact). Author the plan with `/minimise:job` or
`/minimise:brainstorm`. This dogfoods the tool: every job is a test of `mini`.

Edit inline only when one of these is true:
- **The change is small:** about 2 files and 30 changed lines at most, and no
  new behavior that needs its own tests. Typos, config values, one-line fixes
  and renames count as small.
- `mini` can't do it: the feature is blocked in `mini`, or the change can't run
  inside a job, such as a migration of the live `~/.minimise` database.
- `mini` itself is broken and you're fixing it so the job can run.

Before you start a job:
- Settle user-facing design (UI, CLI commands, naming) with the user first. A
  job builds a design that has already been decided. It doesn't decide one.
- Split the work into tasks one agent can finish in one session, each about
  45 minutes or less. Give every task numbered acceptance criteria that someone
  can check against the diff.
- Give every task a `tests` post-hook and a `review-implementation` post-hook.
- Don't edit this checkout while a job or loop runs on it. Jobs commit here.

A COMPLETED job is not proof that the work is done. When a job finishes, read
its diff and check every acceptance criterion of every task. Report each one as
**done**, with evidence (a `file:line` or a test name), or **not done**. List
the not-done items first. Close the gaps with a follow-up job, or inline if the
fix is small by the rule above.

## If You Are a Task Agent in a `mini` Job

This section applies when your prompt contains "You are executing a task in a
multi-agent plan execution system".

- Do the whole task. If you can't finish an item, say so plainly. Never report
  a partial result as complete.
- Before you write your summary, re-read the task's Goal and Description, list
  every requirement in them, and check each one against your diff.
- Start your summary with that checklist. Mark each line `done`, with evidence
  (a `file:line` or a test name), or `NOT DONE`, with the reason. Don't write
  "all done" or "implemented everything" unless every line says `done`.
- Prove the change works: run the tests you added or touched, then the full
  suite (`pytest tests/`). For web UI changes, assert on the rendered HTML and
  the JSON API with Flask's test client, following `tests/test_view_routes.py`.
  Don't use headless browsers or screenshots: headless Chrome hung on these
  pages, so its results can't be trusted here.
- In your handoff, name anything left unfinished under "Current state". The
  next task builds on what you say is there.
