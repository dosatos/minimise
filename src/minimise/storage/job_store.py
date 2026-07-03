"""JobStore — the single owner of job/task persistence (SQLite + jobs_dir).

Everything above this layer (loop, executors, CLI, API) speaks job/task
vocabulary — ``mark_running``, ``record_completed`` — and never touches the
Database or the filesystem directly.
"""

import functools
import os
import yaml
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from minimise.models import Job, Task, Execution, JobStatus, TaskStatus, Plan
from minimise.storage.database import Database, _UNSET
from minimise.utils import ensure_directory, new_id


def _pid_alive(pid) -> bool:
    """True if a process with this pid exists. None pid → dead (never os.kill(None))."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours


def _reconciled(fn):
    """Pass a load result (Job / Optional[Job] / list[Job]) through liveness reconcile."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        return self._reconcile(fn(self, *args, **kwargs))
    return wrapper


class JobStore:
    def __init__(self, db: Database, jobs_dir: Path):
        self.db = db
        self.jobs_dir = ensure_directory(jobs_dir)

    # --- job lifecycle ---------------------------------------------------

    def create(self, plan: Plan, base_commit: str, plan_path: str) -> Job:
        """Persist a new job + its tasks from a parsed plan, and cache the plan."""
        job_id = new_id("job")
        job = Job(
            id=job_id,
            name=plan.name,
            status=JobStatus.PENDING,
            plan_path=plan_path,
            base_commit=base_commit,
            created_at=datetime.utcnow(),
        )
        job.tasks = [
            Task(
                id=new_id("task"),
                job_id=job_id,
                name=pt.name,
                description=pt.description,
                goal=pt.goal,
                assignee=pt.assignee,
                status=TaskStatus.PENDING,
                created_at=datetime.utcnow(),
                base_commit=base_commit,
                estimated_duration_min=pt.estimated_duration_min,
            )
            for pt in plan.tasks
        ]

        self.db.create_job_with_tasks(job, job.tasks)

        job_dir = ensure_directory(self.jobs_dir / job_id)
        with open(job_dir / "plan.yaml", "w") as f:
            yaml.dump(plan.model_dump(), f)

        return job

    @_reconciled
    def load(self, job_id: str) -> Optional[Job]:
        """Load a job with all its tasks attached, or None if not found."""
        job = self.db.get_job(job_id)
        if not job:
            return None
        job.tasks = self.db.list_tasks_for_job(job_id)
        return job

    @_reconciled
    def load_many(self, limit=None) -> list[Job]:
        """List jobs (no tasks attached — matches db.list_jobs) with liveness reconciled."""
        return self.db.list_jobs(limit=limit)

    def _reconcile(self, result):
        """Downgrade any dead RUNNING job(s) in result to FAILED, in-place. Idempotent."""
        jobs = result if isinstance(result, list) else [] if result is None else [result]
        for job in jobs:
            if job.status == JobStatus.RUNNING and not _pid_alive(job.pid):
                self.mark_job_failed(job.id)
                job.status = JobStatus.FAILED
        return result

    def mark_job_running(self, job_id: str) -> None:
        self.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow(), pid=os.getpid())

    def mark_job_completed(self, job_id: str) -> None:
        self.db.update_job_status(job_id, JobStatus.COMPLETED, completed_at=datetime.utcnow())

    def mark_job_failed(self, job_id: str) -> None:
        self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())

    def mark_job_stopped(self, job_id: str) -> None:
        self.db.update_job_status(job_id, JobStatus.STOPPED, completed_at=datetime.utcnow())

    # --- task lifecycle --------------------------------------------------

    def set_task_base_commit(self, task: Task, base_commit: str) -> None:
        task.base_commit = base_commit
        self.db.update_task(task)

    def mark_running(self, task: Task, attempt: int) -> None:
        """Mark a task RUNNING for the given attempt (stamps started_at on attempt 0).

        Also opens an Execution row for this attempt — both writes in one transaction.
        """
        task.retries = attempt
        with self.db.transaction() as conn:
            self.db.update_task_status(
                task.id, TaskStatus.RUNNING,
                started_at=datetime.utcnow() if attempt == 0 else None,
                # attempt 0 = a fresh (re-)run: clear any stale completed_at from a
                # prior FAILED/STOPPED so duration math doesn't render negative.
                completed_at=None if attempt == 0 else _UNSET,
                conn=conn,
            )
            self.db.save_execution(Execution(
                task_id=task.id, attempt=attempt, job_id=task.job_id, execution_type="task",
                status=TaskStatus.RUNNING, started_at=datetime.utcnow(),
            ), conn=conn)

    def record_attempt(self, task: Task, attempt: int, output: str, exit_reason: str = "", ended_at: Optional[datetime] = None) -> None:
        """Record a failed-but-retrying attempt (back to PENDING).

        ``output`` is no longer stored — job.log is the sole narration store; the
        executor writes the failure detail there. exit_reason carries the class.
        """
        with self._close_attempt(task, attempt, TaskStatus.FAILED, ended_at=ended_at, exit_reason=exit_reason) as conn:
            self.db.update_task_status(task.id, TaskStatus.PENDING, conn=conn)

    def record_completed(self, task: Task, output: str, diff: str, commit_sha: Optional[str] = None, exit_reason: str = "success", ended_at: Optional[datetime] = None) -> None:
        """Persist a successful task: save its diff and mark it COMPLETED."""
        if task.base_commit:
            self._save_diff(task, diff)
        with self._close_attempt(
            task, task.retries, TaskStatus.COMPLETED,
            ended_at=ended_at,
            diff_path=task.diff_path, commit_sha=commit_sha,
            exit_reason=exit_reason,
        ) as conn:
            self.db.update_task_status(
                task.id, TaskStatus.COMPLETED, retries=task.retries,
                completed_at=datetime.utcnow(), conn=conn,
            )

    def mark_task_failed(self, task: Task, output: str, exit_reason: str = "", ended_at: Optional[datetime] = None) -> None:
        with self._close_attempt(task, task.retries, TaskStatus.FAILED, ended_at=ended_at, exit_reason=exit_reason) as conn:
            self.db.update_task_status(
                task.id, TaskStatus.FAILED, retries=task.retries,
                completed_at=datetime.utcnow(), conn=conn,
            )

    @contextmanager
    def _close_attempt(self, task: Task, attempt: int, status: TaskStatus, ended_at: Optional[datetime] = None, **exec_fields):
        """Run the caller's task-status write in a txn, then close the attempt's
        Execution row (preserving its started_at) in the SAME transaction.

        The started_at lookup runs first, OUTSIDE the txn: hooks now share task_id
        and attempt=0, so filtering by attempt alone would grab the pre_task hook's.
        """
        existing = next(
            (e for e in self.db.list_executions_for_task(task.id)
             if e.attempt == attempt and e.execution_type == "task"),
            None,
        )
        with self.db.transaction() as conn:
            yield conn
            self.db.save_execution(Execution(
                task_id=task.id, attempt=attempt, job_id=task.job_id, execution_type="task",
                status=status,
                started_at=existing.started_at if existing else None,
                completed_at=ended_at or datetime.utcnow(),
                **exec_fields,
            ), conn=conn)

    def save_execution(self, execution: Execution) -> None:
        """Persist an execution row (e.g. a per-task hook)."""
        self.db.save_execution(execution)

    def list_executions_for_job(self, job_id: str) -> list[Execution]:
        """All executions for a job, in timeline order."""
        return self.db.list_executions_for_job(job_id)

    def mark_task_stopped(self, task: Task) -> None:
        self.db.update_task_status(task.id, TaskStatus.STOPPED, completed_at=datetime.utcnow())

    # --- artifacts (spoken in domain terms, backed by jobs_dir) ----------

    def load_plan(self, job_id: str) -> Plan:
        """Re-read the cached plan for a job."""
        return Plan.from_yaml(self.jobs_dir / job_id / "plan.yaml")

    def task_dir(self, job_id: str, task_id: str) -> Path:
        return ensure_directory(self.jobs_dir / job_id / "tasks" / task_id)

    def job_log_path(self, job_id: str) -> Path:
        """The one-file-per-job live narration log (flat append-only file)."""
        return ensure_directory(self.jobs_dir / job_id) / "job.log"

    def handoff_path(self, job_id: str, task_id: str, attempt: int) -> Path:
        """Per-attempt handoff file, outside the repo so auto-commit can't sweep it in."""
        d = ensure_directory(self.jobs_dir / job_id / "handoffs" / task_id)
        return d / f"attempt-{attempt}.md"

    def _save_diff(self, task: Task, diff: str) -> None:
        diff_path = self.task_dir(task.job_id, task.id) / "diff.txt"
        diff_path.write_text(diff)
        task.diff_path = str(diff_path)
        self.db.update_task_diff_path(task.id, task.diff_path)


def demo():
    """Self-check: create → load round-trips a job and its tasks through the store."""
    import tempfile
    from minimise.models import PlanTask

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        db = Database(tmp / "test.db")
        db.init_db()
        store = JobStore(db, tmp / "jobs")

        plan = Plan(name="Demo", tasks=[
            PlanTask(id="t1", name="One", description="d", goal="g", estimated_duration_min=5),
        ])
        job = store.create(plan, base_commit="abc123", plan_path="demo.yaml")

        loaded = store.load(job.id)
        assert loaded is not None
        assert loaded.name == "Demo"
        assert loaded.base_commit == "abc123"
        # Task ids are generated (prefixed), not taken from the plan's PlanTask.id.
        assert len(loaded.tasks) == 1 and loaded.tasks[0].id.startswith("task-")
        assert job.id.startswith("job-")
        assert store.load_plan(job.id).name == "Demo"

        # Execution history: a failed attempt then a successful one.
        task = loaded.tasks[0]
        store.mark_running(task, 0)
        store.record_attempt(task, 0, "boom")
        store.mark_running(task, 1)
        store.record_completed(task, "done", diff="", commit_sha="deadbeef")
        execs = db.list_executions_for_task(task.id)
        assert [e.attempt for e in execs] == [0, 1], execs
        assert execs[0].status == TaskStatus.FAILED
        assert execs[1].status == TaskStatus.COMPLETED and execs[1].commit_sha == "deadbeef"
        assert all(e.started_at and e.completed_at for e in execs), "timestamps preserved on close"

        store.mark_job_completed(job.id)
        assert store.load(job.id).status == JobStatus.COMPLETED
        print("OK")


if __name__ == "__main__":
    demo()
