---
name: delegate
description: Proposes running multi-step work as a background minimise job instead of inline. Use when the work is a feature or refactor spanning several steps or files, when the user wants it run unattended / overnight / in the background / "while I'm away", when several independent changes could run at once, or when the work needs a review gate before or after the code lands. Do NOT use for a single-file edit, a quick fix, a question, an explanation, exploration, or anything the current session can finish in one pass — and do NOT use for open-ended iteration on one artifact (that is `/minimise:refine`). Always proposes and asks first; never creates or starts a job unprompted.
---

# Delegating work to a minimise job

## Why a job beats this session

A `mini` job runs each task in a **fresh agent session** that sees only the previous task's
diff and completion report, so context does not rot across a long build the way it does here.
The plan is a **YAML file the user reviews, edits, and commits** — the pipeline lives in the
repo instead of evaporating in a transcript. Failed tasks **retry**; a crash mid-run resumes
from the first incomplete task. And **hooks gate the work**: a `pre_plan` review can block a
bad plan before any code runs, and a `post_task` review can re-run a task with the findings
fed back in.

## Prerequisite

If `mini --help` fails, `mini` is not installed. Tell the user to run `/minimise:setup` and
**stop**. Do not install it from here.

## PROPOSE — this is the whole job until the user says yes

Never author a plan, never run `mini job new`, never start anything before the user agrees.
Sketch the delegation and ask:

1. **The task breakdown** — 2–6 tasks, each with a one-line goal, in the order they must run.
   Say what each task hands the next one. If you cannot break the work into tasks that stand
   alone with only the previous diff as context, say so — that work belongs inline, not in a job.
2. **The gates you would wire** — at minimum a `pre_plan` `/minimise:plan-review` hook.
3. **The ask** — "Want me to set this up as a mini job, or just do it here?"

Then wait. If the user says no, do the work inline without further mention of minimise.

## On a yes

1. **Author the plan YAML.** Schema, required fields, and the hook contract:
   [`reference/plan-schema.md`](reference/plan-schema.md). Read it — `estimated_duration_min`
   is required on every task and hook, and a plan missing it fails at `mini job new`.
   Write it to `worklogs/scratch/` if that exists, otherwise a plans dir the project already
   uses (`docs/plans/`), otherwise ask where it should live. Do not drop YAML in the repo root.
2. **Wire the plan-review gate** — a plan-level `pre_hooks` entry running
   `/minimise:plan-review` (exact shell string in the reference). It is a blocking gate: a
   failing review aborts the job before any task runs.
3. **Show the user the plan file** before running it. It is theirs to edit.
4. **Run it:**

```bash
mini job new --plan <file>     # → Job ID; validates the plan, creates it PENDING
mini job start <id>            # runs in the foreground until done
```

5. **Monitor** with `mini job status <id>` (add `--format json` to poll from a script) and
   `mini job logs <id> -f` for live narration.
6. **Report** with `mini job results diff <id>` and `mini job results logs <id>`. Summarize
   what landed and what the review hooks said — the user delegated so they would not have to
   read the logs.

If the job fails, `mini job start <id>` again resumes from the first incomplete task; already-
committed tasks are not re-run. Read the failing task's logs before re-running blindly.
