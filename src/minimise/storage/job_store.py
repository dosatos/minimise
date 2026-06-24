"""JobStore — the single owner of job/task persistence (SQLite + jobs_dir).

Everything above this layer (loop, executors, CLI, API) speaks job/task
vocabulary — ``mark_running``, ``record_completed`` — and never touches the
Database or the filesystem directly.
"""

import uuid
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional

from minimise.models import Job, Task, JobStatus, TaskStatus, Plan
from minimise.storage.database import Database
from minimise.utils import ensure_directory


class JobStore:
    def __init__(self, db: Database, jobs_dir: Path):
        self.db = db
        self.jobs_dir = ensure_directory(jobs_dir)

    # --- job lifecycle ---------------------------------------------------

    def create(self, plan: Plan, base_commit: str, plan_path: str) -> Job:
        """Persist a new job + its tasks from a parsed plan, and cache the plan."""
        job_id = str(uuid.uuid4())
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
                id=pt.id,
                job_id=job_id,
                name=pt.name,
                description=pt.description,
                goal=pt.goal,
                status=TaskStatus.PENDING,
                created_at=datetime.utcnow(),
                base_commit=base_commit,
                estimated_duration_min=pt.estimated_duration_min,
            )
            for pt in plan.tasks
        ]

        self.db.create_job(job)
        for task in job.tasks:
            self.db.create_task(task)

        job_dir = ensure_directory(self.jobs_dir / job_id)
        with open(job_dir / "plan.yaml", "w") as f:
            yaml.dump(plan.model_dump(), f)

        return job

    def load(self, job_id: str) -> Optional[Job]:
        """Load a job with all its tasks attached, or None if not found."""
        job = self.db.get_job(job_id)
        if not job:
            return None
        job.tasks = self.db.list_tasks_for_job(job_id)
        return job

    def mark_job_running(self, job_id: str) -> None:
        self.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())

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
        """Mark a task RUNNING for the given attempt (stamps started_at on attempt 0)."""
        task.retries = attempt
        self.db.update_task_status(
            task.id, TaskStatus.RUNNING,
            started_at=datetime.utcnow() if attempt == 0 else None,
        )

    def record_attempt(self, task: Task, attempt: int, output: str) -> None:
        """Record a failed-but-retrying attempt (back to PENDING with the failure note)."""
        self.db.update_task_status(
            task.id, TaskStatus.PENDING, output=f"Attempt {attempt} failed: {output}"
        )

    def record_completed(self, task: Task, output: str, diff: str) -> None:
        """Persist a successful task: save its diff and mark it COMPLETED."""
        if task.base_commit:
            self._save_diff(task, diff)
        self.db.update_task_status(
            task.id, TaskStatus.COMPLETED, output=output, retries=task.retries,
            completed_at=datetime.utcnow(),
        )

    def mark_task_failed(self, task: Task, output: str) -> None:
        self.db.update_task_status(
            task.id, TaskStatus.FAILED, output=output, retries=task.retries,
            completed_at=datetime.utcnow(),
        )

    def mark_task_stopped(self, task: Task) -> None:
        self.db.update_task_status(task.id, TaskStatus.STOPPED, completed_at=datetime.utcnow())

    # --- artifacts (spoken in domain terms, backed by jobs_dir) ----------

    def load_plan(self, job_id: str) -> Plan:
        """Re-read the cached plan for a job."""
        return Plan.from_yaml(self.jobs_dir / job_id / "plan.yaml")

    def hooks(self, job_id: str) -> tuple[str, str]:
        """Return (pre_plan_hook, post_plan_hook) from the cached plan (pydantic extras)."""
        plan = self.load_plan(job_id)
        return getattr(plan, "pre_plan_hook", "") or "", getattr(plan, "post_plan_hook", "") or ""

    def task_dir(self, job_id: str, task_id: str) -> Path:
        return ensure_directory(self.jobs_dir / job_id / "tasks" / task_id)

    def _save_diff(self, task: Task, diff: str) -> None:
        diff_path = self.task_dir(task.job_id, task.id) / "diff.txt"
        diff_path.write_text(diff)
        self.db.update_task_diff_path(task.id, str(diff_path))


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
        assert len(loaded.tasks) == 1 and loaded.tasks[0].id == "t1"
        assert store.hooks(job.id) == ("", "")
        assert store.load_plan(job.id).name == "Demo"

        store.mark_job_completed(job.id)
        assert store.load(job.id).status == JobStatus.COMPLETED
        print("OK")


if __name__ == "__main__":
    demo()
