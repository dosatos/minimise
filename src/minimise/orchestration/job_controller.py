"""JobController — job lifecycle and process control.

Owns creating jobs, starting/stopping a job, and status lookups. It loads the
job/plan and marks RUNNING/COMPLETED/FAILED around the run; the run loop
itself lives on JobExecutor; per-task work lives in TaskExecutor; persistence
lives in JobStore.
"""

from pathlib import Path
from typing import Optional

from minimise.models import Job, JobStatus, TaskStatus, Plan
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.task_executor import TaskExecutor
from minimise.orchestration.hook_executor import HookExecutor
from minimise.orchestration.job_executor import JobExecutor
from minimise.utils import ensure_directory


class JobController:
    """Orchestrates job creation, process control, and status callbacks."""

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, repo_path: Path):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = ensure_directory(jobs_dir)
        self.repo_path = Path(repo_path)
        self.store = JobStore(db, jobs_dir)
        self.task_executor = TaskExecutor(db, git_tracker, jobs_dir)
        self.hook_executor = HookExecutor()
        self.executor = JobExecutor(self.task_executor, self.hook_executor, git_tracker)

    @classmethod
    def from_paths(cls, db, repo_path, jobs_dir) -> "JobController":
        """Build a controller, wiring a GitTracker for ``repo_path``."""
        return cls(db, GitTracker(Path(repo_path)), jobs_dir, repo_path)

    def create_job(self, plan_path: Path) -> Optional[Job]:
        """Create a job from a plan.yaml file, or return None if creation failed."""
        is_clean, message = self.git_tracker.validate_clean_state()
        if not is_clean:
            print(f"Error: {message}")
            return None

        base_commit = self.git_tracker.get_current_commit()
        if not base_commit:
            print("Error: Could not get current commit")
            return None

        try:
            plan = Plan.from_yaml(plan_path)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return None

        return self.store.create(plan, base_commit, str(plan_path))

    def get_job_status(self, job_id: str) -> Optional[Job]:
        """Get a job with all its tasks attached, or None if not found."""
        return self.store.load(job_id)

    def start_job(self, job_id: str) -> bool:
        """Run a PENDING job to completion in-process; return True on success."""
        job = self.store.load(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        if job.status != JobStatus.PENDING:
            print(f"Job must be in PENDING state (current: {job.status.value})")
            return False

        try:
            plan = self.store.load_plan(job_id)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return False

        self.store.mark_job_running(job_id)
        success = self.executor.execute(job, plan)
        if success:
            self.store.mark_job_completed(job_id)
        else:
            self.store.mark_job_failed(job_id)
        return success

    def stop_job(self, job_id: str) -> bool:
        """Stop a job: mark it and its RUNNING/PENDING tasks STOPPED."""
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        self.store.mark_job_stopped(job_id)
        for task in self.db.list_tasks_for_job(job_id):
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                self.store.mark_task_stopped(task)

        return True
