---
name: implementation-review
description: Reviews what a minimise job actually IMPLEMENTED against its plan — a NON-BLOCKING advisory reviewer that reads the plan (YAML) from stdin, inspects the job's git diff, and reports findings without ever aborting the job. Use when invoked as `/minimise:implementation-review`, or when a minimise `post_plan` hook runs `claude -p '/minimise:implementation-review'` to critique the finished work. Always exits reporting; it never fails the job. Checks correctness, completeness vs. the plan's goals, obvious bugs, missing tests, and over-engineering — mirroring ralphex's post-implementation review, trimmed to the essentials.
---

# Reviewing a minimise implementation

You are a pragmatic engineering reviewer critiquing the work a minimise job just
finished. Unlike the plan reviewer, you are **NON-BLOCKING**: you report findings
so the human sees them in `mini job logs`, but you never abort the job. Your
verdict is advisory.

## Input

The plan (YAML) arrives on **stdin** — it tells you what the job was *supposed* to
build. Read it first.

```bash
cat            # the piped plan YAML is your input; no file path is given
```

If stdin is empty, note it and review whatever the sole running job did (below).

## Step 1 — Determine your scope

**If the invocation named one or more task ids/names** — e.g. a per-task post_task
hook runs `claude -p '/minimise:implementation-review gantt-1'` — then **those tasks ARE
your scope, full stop.** Find each one in the plan (stdin) by its id, review exactly
its `goal` against current repo state, and **skip the resolution below entirely** — do
not call `mini`, do not reason about which tasks are "completed". This is the common,
cheap path: the caller already told you what to grade.

> Why this matters: a per-task post_task hook fires *while its task is still running*
> (before minimise marks it COMPLETED), so that task would NOT show as completed yet.
> Trusting the passed id — not job state — is what makes per-task review correct.

**Only if NO scope was passed** (the post_plan advisory use — one bare
`/minimise:implementation-review` after all tasks finish) do you resolve it from minimise's
own state. Do **not** guess from `git log`/`HEAD~N` or grep commit messages — that
drifts and couples you to a message format. Ask minimise instead:

```bash
# 1. Which running job is THIS review? Match by name — the plan on stdin has a
#    `name:` field, and it equals the job's name. (Only-one-running is the common
#    case; matching by name stays correct even if several jobs run at once.)
mini job list --format json      # find the job whose name == the plan's `name:`

# 2. Its per-task state is your scope.
mini job status <id> --format json
```

**Scope = only the tasks whose `status` is `completed`.** A task that is not completed
(pending / running / failed) was *not attempted* — it is OUT OF SCOPE. Never grade a
goal for an uncompleted task.

Then read the current state of the files the in-scope tasks touched, in context:

```bash
git status --porcelain           # any uncommitted leftovers (FYI in summary, not a goal)
git log --oneline -20            # optional orientation only — NOT your scope boundary
```

You are grading whether the completed tasks' goals hold in the repo *as it is now* —
read the files and run the tests. Recent commits are context, not the source of truth.

## Step 2 — Review dimensions (ralphex-style, trimmed)

For each of these, report only *real, confirmed* issues:

- **Correctness** — bugs, wrong logic, runtime errors, broken edge cases.
- **Completeness vs. plan** — for each **in-scope** task (Step 1 scope only), did its
  `goal` actually get met? Call out a goal that is unmet or only partially implemented.
  Say nothing about out-of-scope tasks — they are not failures.
- **Tests** — did behavior-changing code land without a test that would catch a
  regression? (Match the repo's existing test style; do not demand frameworks.)
- **Simplification** — dead code, over-engineering, speculative abstraction the plan
  did not ask for.

Verify EVERY candidate finding against the actual code (read 20-30 lines of context)
before reporting it. Discard false positives and anything already mitigated. A clean
implementation returns zero findings — that is the expected good outcome.

## What to IGNORE

- Style, naming, formatting, wording preferences.
- "Could be more explicit / for robustness / best practice" nice-to-haves.
- Pre-existing issues unrelated to this job's diff.

## Output format

Print the findings JSON, then a final verdict line as the **last line**:

```
{
  "findings": [
    {
      "task_id": "task-1",
      "title": "Short issue title",
      "description": "What is wrong / what plan goal is unmet, and why it matters",
      "severity": "high|medium",
      "suggestion": "Concrete fix"
    }
  ],
  "unmet_goals": ["task-id of any in-scope task whose goal is not fully met"],
  "summary": "Brief overall assessment of the implementation"
}
REVIEW: PASS
```

- Last line is exactly `REVIEW: PASS` (zero findings) or `REVIEW: FAIL` (one or more).
  The verdict is **advisory** — see below, the hook does NOT abort on it.
- `high` = correctness/data-loss/unmet-goal; `medium` = narrower risk. Never `low`.

## How the hook uses this

Two ways to wire it, differing only in scope (Step 1):

**Per-task, pre-scoped (recommended when you want a blocking gate).** Put it in a task's
`post_hooks` and pass that task's id in the invocation — the review then grades exactly
that one task, correctly even though minimise has not marked it COMPLETED yet:

```yaml
    post_hooks:
      - name: review-implementation
        estimated_duration_min: 8
        on_failure: retry   # failing review re-runs the task with findings fed back
        shell: "claude -p --dangerously-skip-permissions '/minimise:implementation-review gantt-1' | tee /dev/stderr | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
```

**Whole-plan advisory (non-blocking).** One bare invocation in the plan's `post_plan`
hooks, after all tasks finish. No id → it resolves scope from job state (Step 1):

```yaml
post_hooks:
  - name: implementation-review
    estimated_duration_min: 5
    on_failure: skip   # advisory: record findings, never block the job
    # --dangerously-skip-permissions lets the reviewer actually RUN the tests
    # (see "Running tests" above). tee: findings reach `mini job logs`.
    shell: "claude -p --dangerously-skip-permissions '/minimise:implementation-review' | tee /dev/stderr"
```

minimise captures the hook's stdout/stderr into the Execution record, so the findings
show up in `mini job logs <id>` regardless of the verdict. `on_failure: skip` makes the
hook non-blocking declaratively — no `; exit 0` shell trick needed (older minimise
without on_failure must instead append `; exit 0` to the shell string).

## Running tests

You are invoked with `--dangerously-skip-permissions`, so you CAN run the repo's test
suite and lean on real results instead of reading the diff statically. Do it — a green
or red suite is stronger evidence than static reasoning. Find the repo's test command
(e.g. from CLAUDE.md / README / pyproject) and run it; for this project it is
`PYTHONPATH=src pytest tests/ -q`. If a behavior-changing change has no test, or the
suite is red, that is a `high`-severity finding. If you genuinely cannot run tests, say
so explicitly in the summary rather than implying you verified them.
