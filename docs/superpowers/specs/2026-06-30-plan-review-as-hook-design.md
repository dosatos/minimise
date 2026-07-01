# Plan review becomes a hook

**Date:** 2026-06-30
**Status:** Approved (pre-customer; breaking changes OK, no backfill)

## Problem

Plan review is a hardcoded `PlanReviewer` gate inside `mini job new`: always on, hard-blocks
job creation, bypassable only via the global `--skip-review` flag. It's a bespoke branch that
deviates from the hook architecture — it can't be renamed, reordered, disabled per-plan, or
composed the way `pre_hooks`/`post_hooks` can. The goal is to let users guard their plans with a
review that is **opt-in**, not forced.

## The shift

Delete the special case. Plan review becomes an ordinary opt-in `pre_plan` hook the user writes
in their plan YAML.

| | Today | After |
|---|---|---|
| Lives in | Hardcoded `PlanReviewer` in `mini job new` | A `pre_plan` hook in the plan YAML |
| Default | Always on | Off (no hook = no review) |
| Runs at | `job new` (blocks creation) | `job start` (first in the run loop) |
| Decision | Hard block on any finding | Hook's exit code decides |
| Ships | Curated prompt + reviewer | Nothing — user brings their own review command |

## Decisions

- **Opt-in locus:** the plan YAML. No hook = no review. The plan is the whole truth.
- **Who writes it:** the user. Nothing auto-injects.
- **When it runs:** at `job start`, first `pre_plan` hook — same place `job_executor.py:30` already
  runs plan pre-hooks.
- **On findings:** the hook's exit code decides. Nonzero aborts the run via the existing
  `_run_hooks`; no special review logic in the framework.
- **What the framework ships:** nothing to distribute. The framework's only job is to make a hook
  *capable* of reviewing — i.e. give the hook the plan. Which review command a user runs is entirely
  their choice: a slash command like `/review-plan`, a raw `claude -p` prompt, an external linter,
  anything. `/review-plan` is a hypothesized example, not a shipped artifact.
- **Verdict → exit code:** owned by the user's `shell:` string, not the framework. `claude -p`
  always exits 0 on a successful run regardless of what the review concluded, so a review-via-agent
  hook must parse the verdict and set the exit code itself (grep a sentinel, or `jq` the JSON).
  Nonzero aborts the run via the existing `_run_hooks`. No wrapper subcommand.
- **Plan handoff:** raw YAML piped on the hook's stdin by `HookExecutor`. This is the one framework
  change that enables review — without it, an agent hook has no plan to read.
- **`plan_reviewer.py`:** deleted.

## What a user writes

The user chooses their own review command. One example, gating on a sentinel the agent prints:

```yaml
pre_hooks:
  - name: review-plan
    estimated_duration_min: 5
    shell: "claude -p 'Review the plan on stdin; end with REVIEW: PASS or REVIEW: FAIL' | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
```

The hook receives the plan YAML on stdin (fed by `HookExecutor`); the review command picks it up.
The exact prompt/command and how the verdict maps to an exit code are the user's concern, not the
framework's.

## Framework changes

1. **Delete the special case.** Remove the `PlanReviewer` gate from `cli/job.py` and drop the
   `--skip-review` flag. `mini job new` always creates the job; the guardrail moves to `job start`
   and becomes a generic hook. The `PLAN_REVIEW_TIMEOUT_SEC` read goes away with it.

2. **Delete `plan_reviewer.py`** and its `PlanReviewer` re-export from `cli/__init__.py`.

3. **Pipe the plan on stdin.** `HookExecutor.run` feeds the plan as raw YAML to the hook's stdin —
   the minimal version of the deferred `HookContext` idea. A hook that ignores stdin is unaffected.

## Why it's cheap

The execution plumbing already exists. `job_executor.py:30` runs `pre_plan` hooks first, and a
nonzero exit already aborts the run. This is mostly *removal* plus a small stdin channel — the
framework ships no review prompt or command.

## Known debt — accepted, improve later

Gating a review through `claude -p` is slower/flakier than the old in-process call and needs the
`claude` CLI present, and the user must hand-write the verdict→exit-code parsing in their `shell:`
string. The old timeout/retry behavior is no longer the framework's concern. Accepted for now;
revisit later (e.g. a documented recipe or optional helper).
