# Plan schema + hook contract

Everything needed to author a plan `mini job new --plan <file>` will accept. Fields not listed
here have no defined product behavior — do not invent them. Unknown plan and task keys are
preserved for compatibility, not interpreted by the runtime.

## Plan

```yaml
plan:                              # top-level `plan:` key is optional; a bare mapping works too
  name: "Implement Feature X"      # required
  briefing: "Free-form context"    # optional, stable context sent to every task
  pre_hooks: []                    # optional, run once before the whole job (the plan gate)
  post_hooks: []                   # optional, run once after the whole job
  tasks:                           # required, at least one; ids must be unique
    - id: task-1                   # required
      name: "Write tests"          # required
      goal: "One-line objective"   # required — prepended to the agent's prompt
      description: "How to do it"  # required — steps/details, may be a multi-line block
      estimated_duration_min: 15   # REQUIRED, positive int. Missing → validation error.
      timeout_min: 30              # optional hard kill deadline; must be >= the estimate
      assignee: reviewer           # optional persona from ~/.minimise/personas.yaml
      pre_hooks: []                # optional, run before this task
      post_hooks: []               # optional, run after this task's commit
```

**Estimates:** `estimated_duration_min` is the agent's time, not a person's. The job page uses
it for projected bars, time remaining and the ETA, so a padded estimate makes all three wrong.
Across 143 measured agent tasks, the total came to about a quarter of the human-style estimates
(median task: 16%), so start from a quarter of what a person would need; most tasks land at
5–30 min. Single tasks vary a lot, so put the safety margin in `timeout_min` (about 3× the
estimate), not in the estimate.

**Goal vs description:** goal is *what* (one line), description is *how* (the steps). Each task
runs in a fresh agent session with the same plan name and non-blank briefing, plus only the
previous task's persisted handoff as evolving context. The briefing aligns constraints,
non-goals, and terminology; it does not expand the current task's scope.

## Hook

```yaml
- name: review-plan                # required, unique within its list
  estimated_duration_min: 5        # REQUIRED, positive int
  timeout_min: 10                  # optional; must be >= the estimate
  shell: "…"                       # required — a bare name is not supported
  on_failure: fail                 # fail (default) | retry | skip
```

`on_failure` may only be non-`fail` on a **task's `post_hooks`**. Anywhere else (plan hooks, any
`pre_hooks`) a non-default value is a validation error.

- `fail` — nonzero exit fails the task/job.
- `retry` — nonzero exit re-runs the task with the hook's output fed back in, capped by the
  task's retry budget. This is the bounded fix-loop.
- `skip` — nonzero exit is recorded and ignored (advisory checks).

## The hook contract

Every hook is a shell command run in the project. Minimise pipes the **plan YAML to the hook's
stdin** and captures stdout/stderr into the Execution record (`mini job logs <id>`). The hook's
**exit code gates**: nonzero blocks.

`claude -p` always exits 0 regardless of the verdict, so an agent reviewer must print a sentinel
that the `shell:` string greps to set the exit code. And do **not** pipe straight into `grep -q`
— that swallows stdout, leaving an exit code with no explanation. `tee /dev/stderr` first, so the
findings are recorded, then grep the copy.

### The plan gate (blocking, before any task runs)

```yaml
pre_hooks:
  - name: review-plan
    estimated_duration_min: 5
    shell: "claude -p '/minimise:review-plan' | tee /dev/stderr | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
```

### The implementation gate (after a task's commit, sees the real diff)

```yaml
post_hooks:
  - name: review-implementation
    estimated_duration_min: 3       # measured median 2.7 min; 1 in 10 runs takes ~9 min
    timeout_min: 15
    on_failure: retry
    shell: "claude -p '/minimise:review-implementation' --dangerously-skip-permissions | tee /dev/stderr | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
```

Any command honoring the contract works — a linter, a `jq` policy check, `pytest -q`. It does not
have to be an agent. For a test-suite hook, time the suite once and use that as the estimate;
measured test hooks have all finished in under a minute.
