---
name: brainstorm
description: Author a mini job or loop plan under control — triage loop-vs-job, run a retrieval-aware interview pre-filled from your personas and past runs, then preview the exact YAML behind an approve/edit/reject gate before handing off to the unchanged `mini job new` / `mini loop new`. Invoked as /minimise:brainstorm.
disable-model-invocation: true
---

# Authoring a minimise plan under control

## What this is

A skill **one level above** the CLI. You review the exact plan and approve it before anything
runs — instead of hand-writing blank-page YAML or trusting a vibe-coded plan you never saw.
It changes **zero** existing CLI behavior; it is purely additive. Delete it and `mini` is
exactly as it was.

## Prerequisite

If `mini --help` fails, tell the user to run `/minimise:setup` and **stop**.

## Triage first — loop or job

A **job** runs fixed, known, finite steps once. A **loop** iterates plan → implement → evaluate
until a goal is met. Infer which from the user's one-liner, then **confirm it with them** — this
choice decides which schema the survey fills, so getting it wrong wastes the whole interview.

## The interview

Reverse-engineer the survey from the triaged schema: for a **loop**, fill `goal` +
`dimensions` + `max_iterations` + reuse; for a **job**, the task breakdown + gates. Four settled
mechanics:

1. **Ask cold, retrieve after (order A).** Ask the problem-framing questions cold *first*, then
   retrieve from the corpus against the fuller statement. Better-conditioned retrieval, and no
   anchoring to the nearest neighbor before the problem is framed.
2. **Every card is escapable.** Each `AskUserQuestion` card carries concrete options **plus** an
   explicit "You suggest" pick **plus** free-text "Other" — delegating back to the agent is
   first-class, not a fallback. In the cold phase "You suggest" grounds in the one-liner; after
   retrieval it is corpus-grounded.
3. **One concept per turn, ~3 cards.** Not all-at-once, not one-field-at-a-time. Concept-grouped:
   (a) goal + stopping condition, (b) dimensions + `max_iterations`, (c) reuse (after retrieval).
4. **Retrieval = a subagent's semantic judgment** over `mini persona list` + recent loops. No
   keyword matching, no embeddings index. It returns a single top pick to fork and the relevant
   personas to reference (**by versioned name, not inlined** — so upgrades propagate). The
   subagent **drafts**; the **main agent asks** — `AskUserQuestion` cannot run in a subagent.

## The gate — approve / edit / reject

Diff-preview the **exact** YAML, then:

- **approve** — write the file, hand off.
- **edit** — conversational re-render: ask "what should change?", regenerate the YAML from the
  plain-language answer, re-preview. Loop until approve or reject.
- **reject** — drop it, no file written.

The approved file is byte-for-byte what runs (Terraform-style). Write it to `worklogs/scratch/`
if that exists, else a plans dir the project already uses — never the repo root.

## Hand off

To the **unchanged** CLI:

```bash
mini job new --plan <file>     # then the job flow — see /minimise:job
mini loop new --plan <file>    # then the loop flow — see /minimise:loop
```

Point at `/minimise:job` and `/minimise:loop` for run/monitor/report — don't duplicate them here.
Wire the `/minimise:review-plan` pre-hook gate into authored job plans, same as `/minimise:job`.
