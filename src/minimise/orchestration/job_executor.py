"""JobExecutor — job lifecycle, the run loop, and process control.

Owns creating jobs, running a plan's tasks sequentially end to end
(``run_job``), launching/stopping the background process, and status-update
callbacks. Per-task work lives in TaskExecutor; persistence lives in JobStore.
"""

import subprocess
import signal
import os
import sys
from pathlib import Path
from typing import Optional

from minimise.models import Job, JobStatus, TaskStatus, Plan
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.task_executor import TaskExecutor
from minimise.orchestration.handover_manager import HandoverManager
from minimise.orchestration.hooks import Hook
from minimise.utils import ensure_directory


class JobExecutor:
    """Orchestrates job creation, process control, and status callbacks."""

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, repo_path: Path, on_job_update=None, on_task_update=None):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = ensure_directory(jobs_dir)
        self.repo_path = Path(repo_path)
        self.store = JobStore(db, jobs_dir)
        self.task_executor = TaskExecutor(db, git_tracker, jobs_dir)
        self.on_job_update = on_job_update
        self.on_task_update = on_task_update

    def notify_job(self, job_id: str) -> None:
        if self.on_job_update:
            self.on_job_update(job_id)

    def notify_task(self, job_id: str, task_id: str) -> None:
        if self.on_task_update:
            self.on_task_update(job_id, task_id)

    def run_job(self, job_id: str) -> bool:
        """Execute an entire job (all tasks sequentially); returns True on success.

        Loads the job and plan, runs the plan hooks, executes each task in order
        while flowing a handover report from one task to the next, and marks the
        job COMPLETED or FAILED. The detached subprocess from ``start_job`` calls
        this via ``python -m minimise.orchestration.job_executor``.
        """
        job = self.store.load(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        try:
            plan = self.store.load_plan(job_id)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return False

        self.store.mark_job_running(job_id)
        self.notify_job(job_id)

        pre_plan, post_plan = self.store.hooks(job_id)
        if not self._run_hook(job_id, Hook(pre_plan), "Pre-plan"):
            return False

        handover = ""
        for idx, task in enumerate(job.tasks):
            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            success, output = self.task_executor.execute_task(
                task, job_id, handover,
                pre_task_hook=getattr(plan_task, "pre_task_hook", "") or "",
                post_task_hook=getattr(plan_task, "post_task_hook", "") or "",
            )
            if not success:
                print(f"Task {task.name} failed: {output}")
                return self._fail_job(job_id)

            # Hand the completed task's report to the next one.
            if idx < len(job.tasks) - 1:
                diff = self.git_tracker.get_diff(job.base_commit) if job.base_commit else ""
                handover = HandoverManager.build_handover_prompt(output, diff, job.tasks[idx + 1])

        if not self._run_hook(job_id, Hook(post_plan), "Post-plan"):
            return False

        self.store.mark_job_completed(job_id)
        self.notify_job(job_id)
        return True

    def _run_hook(self, job_id, hook: Hook, label: str) -> bool:
        """Run a plan-level hook; fail the job and return False if it errors."""
        success, output = hook.run()
        if not success:
            print(f"{label} hook failed: {output}")
            self._fail_job(job_id)
            return False
        return True

    def _fail_job(self, job_id) -> bool:
        """Mark the job FAILED, notify, and return False (the loop's failure result)."""
        self.store.mark_job_failed(job_id)
        self.notify_job(job_id)
        return False

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

    def start_job(self, job_id: str) -> Optional[int]:
        """Spawn a PENDING job's execution loop as a detached subprocess; return its PID."""
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return None

        if job.status != JobStatus.PENDING:
            print(f"Job must be in PENDING state (current: {job.status.value})")
            return None

        try:
            # start_new_session=True puts the child in its own process group so
            # stop_job can killpg the whole tree. Args go via argv (see __main__).
            process = subprocess.Popen(
                [
                    sys.executable, "-m", "minimise.orchestration.job_executor",
                    str(self.db.db_path), str(self.repo_path), str(self.jobs_dir), job_id,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            pid = process.pid
            self.store.set_job_pid(job_id, pid)
            self.notify_job(job_id)
            return pid

        except Exception as e:
            print(f"Error starting job: {e}")
            return None

    def stop_job(self, job_id: str) -> bool:
        """Stop a job: SIGTERM its subprocess (if any), then mark it and its tasks STOPPED."""
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        if job.pid is not None:
            try:
                os.killpg(os.getpgid(job.pid), signal.SIGTERM)
            except ProcessLookupError:
                # Process already terminated; still finalize state below.
                pass
            except Exception as e:
                print(f"Error stopping job: {e}")
                return False

        self.store.mark_job_stopped(job_id)
        for task in self.db.list_tasks_for_job(job_id):
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                self.store.mark_task_stopped(task)
                self.notify_task(job_id, task.id)

        self.notify_job(job_id)
        return True


def _main() -> None:
    """Detached subprocess entry point: run one job by id (args via argv)."""
    db_path, repo_path, jobs_dir, job_id = sys.argv[1:5]
    executor = JobExecutor(
        Database(Path(db_path)),
        GitTracker(Path(repo_path)),
        Path(jobs_dir),
        Path(repo_path),
    )
    executor.run_job(job_id)


if __name__ == "__main__":
    _main()
