---
name: setup
description: Verify and install the prerequisites for minimise — the `mini` CLI, the `claude` CLI, and a git repo.
disable-model-invocation: true
---

# Setting up minimise

Get `mini` working in the user's current project. **Verify first, install only what is
missing, and write nothing into their repo.**

## Do not touch the project

This skill is read-only with respect to the user's repository. It must NOT create or edit
`.claude/settings.json`, hooks, plan files, scaffolding, or any other file in their project.
All minimise state (config, database, job dirs) lives in `~/.minimise/`, which is created
automatically on first run. If the user asks for project scaffolding, that is a different
conversation — say so rather than quietly writing files.

## 1. Check the three prerequisites

```bash
mini --help          # the minimise CLI (there is no --version flag)
claude --version     # the agent harness minimise shells out to for every task
git rev-parse --show-toplevel   # jobs run inside a git repo; commits/diffs track task output
```

Report each as present or missing before doing anything about it.

- **`claude` missing** — minimise cannot execute a single task without it. Point the user at
  https://docs.anthropic.com/en/docs/claude-code and stop; do not try to install it for them.
- **Not a git repo** — jobs commit each task's work, so there is nothing to commit against.
  Tell the user to `git init` (or move to a repo) and stop. Do not `git init` for them.
- **`mini` missing** — install it (below).

## 2. Install `mini` if it is missing

Requires Python 3.9+. From a local clone:

```bash
pip install -e .            # run from the minimise repo root
```

Or straight from the repo:

```bash
pip install git+https://github.com/dosatos/minimise.git
```

Then **re-verify** — an install that does not put `mini` on `PATH` is not an install:

```bash
mini --help
```

If it still is not found, the likely cause is a `pip` that installed into a different Python
than the one on `PATH` (pyenv/venv mismatch). Say that plainly and show `which -a python3 pip`
rather than guessing.

## 3. Confirm and hand off

When all three checks pass, tell the user:

- State lives in `~/.minimise/` (auto-created on first run) — nothing was added to their project.
- The commands they can type — nothing fires on its own:
  - `/minimise:brainstorm` — author a job or loop plan under control before handing off.
  - `/minimise:job` — run multi-step work as a background job.
  - `/minimise:loop` — iterate on one artifact until the goal is met.
  - `/minimise:review-plan` — review a plan YAML (also usable as a blocking `pre_plan` hook).
  - `/minimise:review-implementation` — review what a job implemented against its plan.
- A quick smoke test if they want one: `mini job list` (empty list = working install).
