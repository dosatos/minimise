"""JobController — job lifecycle and process control.

Owns creating jobs, starting/stopping a job, and status lookups. It loads the
job/plan and marks RUNNING/COMPLETED/FAILED around the run; the run loop
itself lives on JobExecutor; per-task work lives in TaskExecutor; persistence
lives in JobStore.
"""

import os
from pathlib import Path
from typing import Optional

from minimise.models import Job, JobStatus, TaskStatus, Plan
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.engine_process import engine_alive, stop_engine
from minimise.orchestration.task_executor import JobStopped, TaskExecutor
from minimise.orchestration.hook_executor import HookExecutor
from minimise.orchestration.job_executor import JobExecutor
from minimise.logging.backend import JsonlLogBackend
from minimise.utils import ensure_directory

# start_job outcomes (a crashed RUNNING job arrives reconciled to FAILED).
RAN_OK = "ran_ok"                 # executed to completion
RAN_FAILED = "ran_failed"         # executed but the run failed
RAN_STOPPED = "ran_stopped"       # stopped (usage limit or `mini job stop`); resumable
BACKED_OFF = "backed_off"         # a live RUNNING job or engine — left alone
ALREADY_COMPLETE = "already_complete"


class JobController:
    """Orchestrates job creation, process control, and status callbacks."""

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, repo_path: Path,
                 *, factory=None, personas: Optional[dict] = None):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = ensure_directory(jobs_dir)
        self.repo_path = Path(repo_path)
        self.store = JobStore(db, jobs_dir)
        self.task_executor = TaskExecutor(
            self.store, git_tracker,
            factory=factory,
            personas=personas,
        )
        self.hook_executor = HookExecutor(
            store=self.store, repo_root=self.repo_path, backend=JsonlLogBackend(),
        )
        self.executor = JobExecutor(self.task_executor, self.hook_executor)

    @classmethod
    def from_paths(cls, db, repo_path, jobs_dir, *,
                    factory=None, personas=None) -> "JobController":
        """Build a controller, wiring a GitTracker for ``repo_path``."""
        return cls(db, GitTracker(Path(repo_path)), jobs_dir, repo_path,
                   factory=factory,
                   personas=personas)

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

    def start_job(self, job_id: str) -> Optional[str]:
        """Idempotent start: run/resume the job, or step aside. Returns an outcome
        constant (RAN_OK / RAN_FAILED / RAN_STOPPED / BACKED_OFF / ALREADY_COMPLETE),
        or None if the job doesn't exist.

        store.load already reconciled the job, so a crashed RUNNING job arrives as
        FAILED; anything still RUNNING is genuinely live.
        """
        job = self.store.load(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return None

        if job.status == JobStatus.RUNNING:
            print(f"Job already running (pid {job.pid})")
            return BACKED_OFF
        if job.status == JobStatus.COMPLETED:
            print("Job already complete")
            return ALREADY_COMPLETE
        # A stopped engine that hasn't exited yet still works the checkout; a
        # second engine beside it would commit its files. (The pid check keeps a
        # new engine that reused the old pid from backing off from itself.)
        if job.pid != os.getpid() and engine_alive(job.pid, "job", job_id):
            print(f"Engine still running (pid {job.pid}). Stop it first: mini job stop {job_id[:8]}")
            return BACKED_OFF

        # PENDING (fresh) or FAILED/STOPPED (resume) both run the same path;
        # execute() skips COMPLETED tasks using the live statuses store.load attached.
        try:
            plan = self.store.load_plan(job_id)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return RAN_FAILED

        # Rows the earlier run left RUNNING (stop or crash) end as interrupted;
        # the resume adds new attempt rows beside them.
        self.store.close_interrupted_executions(job_id)
        self.store.mark_job_running(job_id)
        self.hook_executor.job_id = job_id
        self.hook_executor.log_path = self.store.job_log_path(job_id)
        try:
            success = self.executor.execute(job, plan)
        except JobStopped:
            # The job is already STOPPED (a task's usage limit, with its reason, or
            # `mini job stop`, seen at a task/hook boundary); FAILED would bury it.
            return RAN_STOPPED
        if success:
            self.store.mark_job_completed(job_id)
        else:
            self.store.mark_job_failed(job_id)
        return RAN_OK if success else RAN_FAILED

    def stop_job(self, job_id: str) -> bool:
        """Stop a job: mark it and its RUNNING/PENDING tasks STOPPED, then kill
        its engine's whole process tree (agent and hooks included), waiting up
        to the grace period. Marking first means an engine that survives the
        kill sees STOPPED at its next task or hook and ends the run itself.

        The kill runs whatever the status: a STOPPED job whose engine is still
        alive is stopped again rather than left beside a refusing start.
        """
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        self.store.mark_job_stopped(job_id)
        for task in self.db.list_tasks_for_job(job_id):
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                self.store.mark_task_stopped(task)

        stop_engine(job.pid, "job", job_id)  # verifies the pid; signals nothing otherwise
        return True
