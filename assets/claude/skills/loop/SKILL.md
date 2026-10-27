---
name: loop
description: Run open-ended iteration on one artifact as a minimise refinement loop — plan → implement → evaluate, repeating until the goal is met or max_iterations is hit. Also the reference for the loop spec — read it before writing, patching, reviewing or explaining one, or answering how a loop or its evaluators work; `mini loop new --help` lists the fields only briefly. Fields: goal, max_iterations, plan_version, loop.plan / loop.implement (prompt | prompt_file | persona, harness, model), loop.evaluate.dimensions (name, rubric, prompt | prompt_file | persona), evaluate.select (all | failed), evaluate.max_concurrent.
---

# Refining an artifact with a minimise loop

## Loop vs job

A **job** runs a fixed task list once — you already know the steps. A **loop** repeats
plan → implement → evaluate against one artifact until the planner decides the goal is met (or
`max_iterations` is hit), with each iteration's critique feeding forward into the next plan.
If the tasks are already known and finite, this is the wrong command — that is `/minimise:job`.

## Prerequisite

If `mini --help` fails, tell the user to run `/minimise:setup` and **stop**.

## PROPOSE — nothing runs until the user says yes

The user asked for a loop, but the goal and the rubric are what actually decide when it stops,
and those are theirs to approve. Never author a spec or run `mini loop new` before they agree.
Put in front of them:

1. **The goal** — one sentence, and it must contain the stopping condition. "Improve the README"
   never terminates; "improve the README until a first-time reader can set up, use, and test the
   project without asking a question" does. The planner reads this to decide when to stop.
2. **The evaluation dimensions** — 2–4 named dimensions with a rubric each. These are what the
   loop scores itself on every iteration, so they are the actual definition of "good enough";
   get them right with the user, not alone.
3. **`max_iterations`** — the ceiling on cost. Suggest **at least 5** unless the work argues
   otherwise. The built-in planner prompt already treats a failing evaluate dimension as a
   default reason to `continue` (it only stops early if its summary states why the failure is
   acceptable) — a low ceiling defeats that by cutting the loop off before it can actually
   converge.
4. **`evaluate.max_concurrent`** — suggest **at least 8** so dimensions fan out fully in one
   round instead of queuing behind a low cap; only lower it if the dimension count is small or
   the work argues for staggering.
5. **The ask** — "Want me to run this as a mini loop, or keep iterating here?"

If the user says no, iterate inline and drop it.

## On a yes

Write the spec (to `worklogs/scratch/` if it exists, else a plans dir the project already uses —
never the repo root). This is the whole schema; there are no other fields:

```yaml
version: "1"
name: Refine the README
goal: Improve the README until a first-time reader can set up, use, and test the project unaided.
max_iterations: 5
loop:
  plan:
    prompt: >
      You are the PLANNER. Read the goal and journal history, then decide the next
      concrete step for the implementer — or stop the loop if the goal is met.
  implement:
    prompt: >
      You are the IMPLEMENTER. Carry out the current plan by editing the working tree,
      then report what you changed.
  evaluate:
    select: all                # all (default) | failed — see below
    max_concurrent: 8          # dimensions fan out in parallel, capped by this (code default 4)
    dimensions:
      - name: clarity          # names must be unique
        rubric: Is the README easy to follow for a first-time reader?
      - name: completeness
        rubric: Does it cover setup, usage, and testing without gaps?
```

`evaluate.select: all | failed`. The default, `all`, runs every dimension every iteration.
`failed` doesn't re-run a dimension whose latest earlier verdict is `pass`; that pass is carried
over. `fail` and `blocked` both count as failed and always re-run. A spec patch re-runs
everything. Before finishing, the loop re-checks carried-over passes: when everything that ran
passes, the carried ones run too, in the same iteration. In the journal, a carried pass has
`evaluated_in_iteration` and a re-check has `"recheck": true`; `mini loop status` marks
re-checks `✓ re-check`. Pick `failed` when a loop has
several dimensions and each iteration's fix usually targets one of them: an iteration then pays
only for the failing evaluators, and the re-check still guards the finish. `evaluate` rejects
unknown keys, so a typo like `selct:` fails at `mini loop new` instead of silently running `all`.

Each of `plan`, `implement`, and every dimension is a *worker*: it may set **at most one** of
`prompt:`, `prompt_file:`, or `persona:`. Setting none uses the built-in step default; setting
two is a validation error. `persona:` accepts either a user-defined name from
`~/.minimise/personas.yaml`, or one of `mini`'s built-in reviewer personas (`mini persona list`
to see them — reserved `mini:` namespace, no config needed). For a document-review loop, pointing
`evaluate.dimensions` at the built-in `mini:doc-review:*` / `mini:software-design:*` personas
gives a blind, multi-lens review; each dimension still needs its own `rubric:` naming the document
and what this run cares about — the persona supplies the reviewer's general judgment style, the
rubric supplies the specifics. Leave `plan`/`implement` unset: the planner reads what the personas
flagged and the implementer fixes it, same as any other loop.

Show the user the spec, then run it:

```bash
mini loop new --plan <file>    # → Loop ID; validates the spec and every persona
mini loop start <id>           # starts in the background, idempotent; returns immediately
mini loop status <id>          # iteration progress, stage timing, per-dimension verdicts
mini loop journal <id>         # the loop's memory: plan/implement/evaluate lines + commits
```

Report back from `mini loop status` and `mini loop journal` — what changed, what the evaluators
said, and whether it stopped because the goal was met or because it ran out of iterations. Those
two endings mean very different things and the user needs to know which one they got.
**`Status: completed` only means the planner chose to stop — it does not mean every dimension
passed.** Always surface the verdict table (or journal verdicts) alongside a "completed" report;
a planner can stop with failing dimensions if it judged that acceptable. End with one line from
`tokens_total` in `mini loop status <id> --format json`: `Tokens: 16.2M (96% cache reads)`, where
the share is `cache_read` / `total`. Leave the line out when `tokens_total` is null.

Token figures: `mini loop status` prints the loop's `Tokens:` line and a `tokens` row per iteration
(Claude Code runs only; `0` = no agent ran, `—` = unknown: not reported, still running, or a loop
from before token tracking); `--format json` has `tokens_total` and each step's `tokens`.
README → Token Usage documents the object.
