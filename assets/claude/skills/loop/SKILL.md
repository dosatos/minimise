---
name: loop
description: Run open-ended iteration on one artifact as a minimise refinement loop — plan → implement → evaluate, repeating until the goal is met or max_iterations is hit.
disable-model-invocation: true
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
3. **`max_iterations`** — the ceiling on cost. Suggest 3 unless the work argues otherwise.
4. **The ask** — "Want me to run this as a mini loop, or keep iterating here?"

If the user says no, iterate inline and drop it.

## On a yes

Write the spec (to `worklogs/scratch/` if it exists, else a plans dir the project already uses —
never the repo root). This is the whole schema; there are no other fields:

```yaml
version: "1"
name: Refine the README
goal: Improve the README until a first-time reader can set up, use, and test the project unaided.
max_iterations: 3
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
    max_concurrent: 2          # dimensions fan out in parallel, capped by this (default 4)
    dimensions:
      - name: clarity          # names must be unique
        rubric: Is the README easy to follow for a first-time reader?
      - name: completeness
        rubric: Does it cover setup, usage, and testing without gaps?
```

Each of `plan`, `implement`, and every dimension is a *worker*: it may set **at most one** of
`prompt:`, `prompt_file:`, or `persona:` (a name from `~/.minimise/personas.yaml`). Setting none
uses the built-in default; setting two is a validation error.

Show the user the spec, then run it:

```bash
mini loop new --plan <file>    # → Loop ID; validates the spec and every persona
mini loop start <id>           # runs to convergence or max_iterations (foreground, idempotent)
mini loop status <id>          # iteration progress, stage timing, per-dimension verdicts
mini loop journal <id>         # the loop's memory: plan/implement/evaluate lines + commits
```

Report back from `mini loop status` and `mini loop journal` — what changed, what the evaluators
said, and whether it stopped because the goal was met or because it ran out of iterations. Those
two endings mean very different things and the user needs to know which one they got.
