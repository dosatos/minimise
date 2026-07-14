# Plan schema + hook contract

Everything needed to author a plan `mini job new --plan <file>` will accept. Fields not listed
here do not exist — do not invent them.

## Plan

```yaml
plan:                              # top-level `plan:` key is optional; a bare mapping works too
  name: "Implement Feature X"      # required
  briefing: "Free-form context"    # optional, extra keys are allowed
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

**Goal vs description:** goal is *what* (one line), description is *how* (the steps). Each task
runs in a fresh agent session and receives only the previous task's git diff and completion
report — so a task must be self-contained given that.

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
    estimated_duration_min: 8
    on_failure: retry
    shell: "claude -p '/minimise:review-implementation' --dangerously-skip-permissions | tee /dev/stderr | grep -q '^REVIEW: FAIL' && exit 1 || exit 0"
```

Any command honoring the contract works — a linter, a `jq` policy check, `pytest -q`. It does not
have to be an agent.
