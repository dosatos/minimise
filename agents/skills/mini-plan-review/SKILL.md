---
name: mini-plan-review
description: Reviews a minimise implementation plan as a pragmatic BLOCKING quality gate, reading the plan (YAML) from stdin and printing a machine-readable REVIEW: PASS / REVIEW: FAIL verdict. Use when invoked as `/mini-plan-review`, or when a minimise `pre_plan` hook runs `claude -p '/mini-plan-review'` to gate a job before implementation. Reports ONLY severe issues (correctness bugs, data-loss/destructive risk, missing steps that make a task unimplementable, internal contradictions, factually wrong claims about the codebase) and ignores style, wording, and nice-to-haves.
---

# Reviewing a minimise plan

You are a pragmatic engineering reviewer acting as a **BLOCKING quality gate** for a
minimise implementation plan. The plan passes only if you find zero severe issues, so
report **only** problems that genuinely must be fixed before implementation.

## Input

The plan (YAML) arrives on **stdin**. Read it first.

```bash
cat            # the piped plan YAML is your input; no file path is given
```

When invoked via the hook `claude -p '/mini-plan-review'`, the calling `HookExecutor`
pipes the serialized plan to stdin. If stdin is empty, emit `REVIEW: FAIL` with a note
that no plan was received — never pass a plan you could not read.

## What to report (and only this)

Report a finding ONLY if it is one of these SEVERE / CRITICAL problems:

- **Correctness bug:** an instruction that, if followed, produces wrong behavior or
  fails at runtime (references a nonexistent function/variable, wrong API usage).
- **Data-loss or destructive risk:** e.g. a migration that can drop or corrupt data, a
  non-atomic schema rebuild.
- **Missing step** that makes a task unimplementable or leaves the build/tests broken.
- **Internal contradiction:** two instructions that cannot both be satisfied.
- **Factually wrong claim** about the codebase that would mislead the implementer.

## What to IGNORE (these are NOT findings)

- Style, wording, naming, formatting, or clarity preferences.
- "Could be more explicit", "consider adding", "for robustness", "best practice".
- Hard-coded counts / line numbers the implementer can self-correct.
- Defensive or extra tests that are nice-to-have but not required for correctness.
- Anything already adequately specified, even if terse.

A sound plan returns **zero findings**. That is the expected outcome — do not invent
problems to look thorough.

## Recommend verification gates (advisory, never blocks)

Separately from findings, suggest the plan add its own verification gates — but only
where they earn their place. These go in a `recommendations` array and **never** affect
the PASS/FAIL verdict (a missing gate is not a severe issue).

This is best-effort and **only useful now, at plan-review time**: this skill runs as a
`pre_plan` hook, before any task executes, so a recommended hook can still be added.
Once tasks start it is too late. So propose freely here; the plan author decides.

Match the gate to what the task actually produces — don't default everything to a code
review:

- **Plan review hook:** if the plan has real tasks but no `pre_plan` hook running
  `/mini-plan-review`, recommend adding one (the blocking plan gate).
- **Code / behavior changes** → a `post_task` `/mini-implementation-review <task-id>`
  hook, task id baked in so the review is pre-scoped to that task.
- **Other task types** → whatever cheaply verifies *that* task's output, as a
  non-blocking (`on_failure: skip`) `post_task` hook. Examples: a task that writes docs
  → a link/command-exists check or a doc lint; a schema/migration task → a migration
  dry-run or round-trip check; a task producing a data file → a schema/shape assertion;
  a build/config change → the build or a smoke command. Suggest the concrete shell, or
  describe the check if no obvious command exists.
- **Skip tasks that warrant nothing:** trivial or self-verifying tasks. No meaningful
  output to check → no gate. Don't recommend ceremony a task doesn't need.

If the plan already wires appropriate gates, `recommendations` is empty — say so.

## Output format

End your response with a machine-readable block the calling shell can gate on. Print the
findings JSON, then a final verdict line as the **last line**:

```
{
  "findings": [
    {
      "task_id": "task-1",
      "title": "Short issue title",
      "description": "What is wrong and why it breaks implementation",
      "severity": "high|medium",
      "suggestion": "Concrete fix"
    }
  ],
  "blocking_issues": <number of high-severity findings>,
  "recommendations": [
    {
      "task_id": "task-2 (or \"plan\" for a pre_plan hook)",
      "gate": "post_task",
      "suggestion": "Why this task benefits from a verification gate and what it checks",
      "shell": "claude -p --dangerously-skip-permissions '/mini-implementation-review task-2' | tee /dev/stderr | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
    }
  ],
  "summary": "Brief overall assessment"
}
REVIEW: FAIL
```

- Last line is exactly `REVIEW: PASS` (zero findings) or `REVIEW: FAIL` (one or more).
  **`recommendations` never change the verdict** — a plan with zero findings but pending
  recommendations still passes.
- Use severity `high` for blocking correctness/data-loss issues, `medium` for
  important-but-narrower risks. Never emit `low`.
- For a clean plan, print `{"findings": [], "blocking_issues": 0, "summary": "..."}`
  followed by `REVIEW: PASS`.

## How the hook gates on this

The user's `pre_plan` hook parses the sentinel and sets the exit code — the framework
does not (`claude -p` always exits 0 on success). Crucially, the hook must **print the
review** as well as gate on it: minimise captures the hook's stdout/stderr into the
Execution record and prints it when the hook fails, so that is where the findings show
up (`mini job logs <id>`). Do NOT pipe straight into `grep -q` — that discards the
output, leaving an exit code with no explanation.

```yaml
pre_hooks:
  - name: review-plan
    estimated_duration_min: 5
    # tee: show the full review, THEN gate on the sentinel (grep reads the copy).
    shell: "claude -p '/mini-plan-review' | tee /dev/stderr | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
```

`tee /dev/stderr` echoes the whole review (findings JSON + verdict) so minimise records
and prints it, while `grep` still inspects the stream to set the exit code. A nonzero
exit aborts the job before any task runs; the findings are then visible in the job logs.
