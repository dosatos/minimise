# Resilient `mini job start` — Design

**Date:** 2026-07-02
**Status:** Approved, ready for implementation plan

## Problem

Jobs run **in the foreground** (`JobController.start_job` blocks in-process,
`job_controller.py:88`). If that process dies — Ctrl-C, closed terminal, crash —
the job is left at `RUNNING` in the DB **forever**. There is no liveness check,
no reconciliation, and no way to continue: `start_job` requires `PENDING`
(`job_controller.py:75`), so a wedged `RUNNING` job can only be deleted.

Completed tasks *are* committed to git atomically per-task
(`task_executor.py:135`), so finished **work** is never lost — only the job
**state** wedges. The gap is purely: (a) detecting a dead vs. live `RUNNING`
job, and (b) continuing from where it stopped.

Note: the README/TESTING docs describe a `mini job resume` command and
background execution. **Neither exists.** Those docs are stale and are corrected
as part of this work.

## Principle

**A task is `COMPLETED` only once it has persisted all its artifacts** — commit,
diff, and **handover**. Today the handover is threaded task-to-task *in memory*
(`job_executor.py:74`) and, when the agent leaves its handoff file empty, the
diff-based fallback is generated in memory and **never written to disk**
(`_read_handoff`, `task_executor.py:154-159`). That in-memory chain is lost on
crash. Enforcing the invariant makes resume trivial: read the previous completed
task's handoff file — completion guarantees it exists.

## Design

No new command, no new schema, no `resume` verb. `mini job start` becomes
idempotent.

### 1. Completion invariant (root fix)

In `TaskExecutor`, on the success path, **before** `record_completed` marks the
task `COMPLETED`, ensure the winning attempt's handoff file
(`handoff_path(job_id, task.id, task.retries)`) is non-empty: if the agent wrote
it, keep it; if empty, write the diff-based fallback
(`HandoverManager.build_handover_prompt(...)`) **to disk**.

- Must run **unconditionally** — independent of `next_task`. Currently the
  handover is only built when `next_task is not None` (`task_executor.py:141`),
  so the **last task never persists a handover**. The fix persists regardless,
  so the invariant holds for every completed task including the last.
- `task.retries` equals the winning attempt number for a COMPLETED task
  (`mark_running` sets `task.retries = attempt`, `job_store.py:92`;
  `record_completed` persists `retries=task.retries`), so
  `attempt-{task.retries}.md` is the correct file. Verified.

### 2. Record the orchestrator PID

`JobStore.mark_job_running` writes `os.getpid()` via the existing
`Database.update_job_status(pid=...)` param (`database.py:384-386`). Schema
already has `jobs.pid` (`database.py:159`) with a migration guard — **no
migration needed**.

### 3. Reconcile — injected once, never hand-called

The wedge (a crashed job stuck at `RUNNING`) must never be *visible* anywhere, so
reconciliation can't be a helper sprinkled across `list`/`status`/`start`. It is
**injected at the single point every read already returns from** — `JobStore`'s
load methods — via a decorator. Write the logic once, maintain it once; every
caller gets it for free.

```python
# job_store.py — the ONE place the logic lives
def _reconciled(fn):
    @wraps(fn)
    def wrap(self, *a, **k):
        return self._reconcile(fn(self, *a, **k))   # accepts a Job or list[Job]
    return wrap

class JobStore:
    @_reconciled
    def load(self, job_id) -> Optional[Job]: ...        # existing
    @_reconciled
    def load_many(self, limit=None) -> list[Job]: ...   # new — wraps db.list_jobs

    def _reconcile(self, job_or_list):
        for job in ([job_or_list] if isinstance(job_or_list, Job) else job_or_list or []):
            if job and job.status == RUNNING and not _pid_alive(job.pid):
                self.mark_job_failed(job.id)             # dead orchestrator → FAILED
                job.status = FAILED
        return job_or_list

def _pid_alive(pid):
    if pid is None: return False        # legacy job, or crash before pid written
    try: os.kill(pid, 0); return True   # alive (also True on PermissionError)
    except ProcessLookupError: return False
```

- **`pid is None`** → not alive → `FAILED`. Never call `os.kill(None, 0)`
  (raises `TypeError`).
- Only *downgrades* dead `RUNNING`→`FAILED`; never touches a live `RUNNING`,
  `PENDING`, `COMPLETED`, `STOPPED`, or already-`FAILED` job. Idempotent,
  side-effect-free on healthy jobs.

**Route every read through the store** (this is what makes it single-point).
`JobStore`'s own docstring already claims it is *"the single owner of job/task
persistence... everything above never touches the Database directly"*
(`job_store.py:1-6`) — a convention **currently violated** by three direct
readers. Point them at the store so the decorator actually covers all reads:

| Caller | Today | Change to |
|---|---|---|
| `cli/_shared.py:101` | `db.get_job` | `store.load` |
| `cli/job.py:140` (`job list`) | `db.list_jobs` | `store.load_many` |
| `interfaces/api_server.py:41,53` | `db.get_job` / `db.list_jobs` | `store.load` / `load_many` |

No caller ever calls `_reconcile` directly; they just load jobs as before and get
correct statuses. `mini job list`, `status`, `start`, and the API all render a
crashed job as `FAILED`, not `RUNNING` — with zero reconcile logic in any of them.

> Excluded from the decorator: `stop_job` reads via `db.get_job` (`job_controller.py:97`)
> to *write* status — leave it on the raw DB read (reconciling a job you're about
> to STOP is pointless), keeping the decorator strictly on the load-for-read path.

### 3b. `start_job` after reconcile

`start_job` loads via `store.load` (already does, `job_controller.py:70`), so the
job is **already reconciled** — a crashed job arrives as `FAILED`, collapsing the
wedge into the existing `FAILED` branch with no extra code:

| Job status (post-reconcile) | Action |
|---|---|
| `PENDING` | run (today's behavior) |
| `RUNNING` (still live) | **back off** — "already running (pid N)" |
| `FAILED` / `STOPPED` | resume |
| `COMPLETED` | no-op — "already complete" |

**Return contract:** `start_job` currently returns `bool` where the CLI maps
`False → "Job failed", exit 1` (`cli/job.py:120`). Back-off and no-op are
neither success nor failure. `start_job` returns a small outcome enum/string —
`ran_ok` / `ran_failed` / `backed_off` / `already_complete` — and the CLI maps
each to its own message + exit code (back-off and already-complete exit 0).

### 4. Resume = skip completed, single `execute` path

`JobExecutor.execute` skips tasks already `COMPLETED` and begins at the first
non-complete task (`PENDING`/`FAILED`/`RUNNING`/`STOPPED`). Implementation
detail: **do not slice `job.tasks`** — iterate the full list and `continue` past
COMPLETED ones, preserving the `plan.tasks[idx]` / `next_task` index alignment
(`job_executor.py:56-57`) that per-task hooks depend on.

- **Handover** for the first resumed task = the **previous completed task's**
  persisted handoff file (guaranteed non-empty by §1); empty string if the first
  non-complete task is task 0.
- **Retry budget starts fresh** automatically — it's a local
  `range(MAX_RETRIES + 1)` in `execute_task` (`task_executor.py:69`), nothing
  persisted gates it. Re-running a `FAILED` task gives it a full budget.
- **Hooks re-run on resume** (approved decision): `execute` keeps a single path.
  `pre_plan` hooks re-run (re-gating a possibly-changed plan is acceptable/
  desirable), per-task hooks run for each executed task, `post_plan` runs at the
  end. No resume flag, no conditional hook logic.

### 5. Re-running a non-PENDING task — state hygiene

A resumed `FAILED`/`STOPPED`/orphaned-`RUNNING` task carries a stale
`completed_at` (`mark_task_stopped`/`mark_task_failed` set it,
`job_store.py:130,166`) while `mark_running` only stamps `started_at` on
attempt 0 and never clears `completed_at` (`job_store.py:96`). On re-run,
**clear `completed_at`** (and re-stamp `started_at`) so `mini job status`
duration math (`cli/job.py:250-251`) doesn't render a negative/bogus duration.

The orphaned open Execution row from a crashed attempt (status `RUNNING`,
`completed_at` NULL) is overwritten cleanly: `save_execution` is
INSERT-OR-REPLACE keyed by `execution_id` which includes the attempt number, and
resume restarts at attempt 0.

### 6. Working tree

**Untouched** — matches current `start` behavior (the clean-tree gate lives only
in `create_job`, `job_controller.py:46`; `start` never checks the tree). The
re-running task's `git add -A` (`git_tracker.py`) sweeps any partial work from
the interrupted attempt into that task's own commit, which is correct — it's the
same task re-running over its own leftovers.

## Explicitly NOT building (YAGNI)

- No background spawning / daemon
- No heartbeat (pid liveness is enough for a local single-user tool)
- No `mini job resume` command
- No dirty-tree gate on `start`

**Known ceiling:** `os.kill(pid, 0)` can misread a **recycled** PID as "alive"
(OS reused a dead job's PID for an unrelated process). Acceptable for a local
single-user tool. Upgrade path if it ever bites: store pid + process start-time
and compare both.

## Testing

- Completion persists a non-empty handoff for every completed task, **including
  the last** (agent-empty → fallback written to disk).
- `mark_job_running` writes `os.getpid()`.
- `_reconcile` downgrades `RUNNING`+dead-pid to `FAILED`; leaves live `RUNNING`
  and all other statuses untouched (idempotent, no side effects on healthy jobs).
- `_reconcile` with `pid is None` → `FAILED` (no `TypeError`).
- The decorator fires on **both** `store.load` (single Job) and `store.load_many`
  (list) — a crashed job in a list is downgraded too.
- `mini job list` / `status` and the API render a crashed job as `FAILED` — proves
  the direct-DB readers were rerouted through the store.
- `start` on `RUNNING` + live pid → backs off, exit 0, job untouched.
- `start` on a crashed job (reconciled to `FAILED`) → resumes.
- `start` on `COMPLETED` → no-op message, exit 0.
- Resume skips COMPLETED tasks and reads the previous task's handoff file.
- Resume of a `FAILED` task gets a fresh retry budget.
- Re-run clears stale `completed_at` (status shows no negative duration).
- **Rewrite** the three existing CLI tests that assert the old behavior:
  `test_start_failed_job_fails`, `test_start_completed_job_fails`,
  `test_start_already_running_job_fails` (`tests/test_cli.py:495-552`) — they now
  assert resume / no-op / back-off respectively.

## Docs to correct (stale `resume`/background claims)

- `README.md` — Quick Start "Resume a failed/stopped job" block, command table
  row, lifecycle diagram, benefits list. Rewrite to the idempotent-`start`
  model; drop background-execution language.
- `TESTING.md` — "RESUME command tests", resume workflow examples.
- `examples/complete-cli-redesign-plan.yaml` — `task-5-resume-command` prose.
- Leave `docs/superpowers/plans/**` historical records as-is.
