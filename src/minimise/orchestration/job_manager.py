"""Job Manager for orchestrating plan execution and task sequencing."""

import subprocess
import signal
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from minimise.models import Job, JobStatus, TaskStatus, Plan
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.task_executor import TaskExecutor
from minimise.utils import ensure_directory


class JobManager:
    """Orchestrates plan execution, task sequencing, and handover between tasks."""

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, repo_path: Path, on_job_update=None, on_task_update=None):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = ensure_directory(jobs_dir)
        self.repo_path = Path(repo_path)
        self.store = JobStore(db, jobs_dir)
        self.task_executor = TaskExecutor(db, git_tracker, jobs_dir)
        self.on_job_update = on_job_update
        self.on_task_update = on_task_update

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
            # stop_job can killpg the whole tree.
            script = f"""
import sys
sys.path.insert(0, '{str(self.jobs_dir.parent.parent)}')
from minimise.storage.database import Database
from minimise.orchestration.job_manager import JobManager
from minimise.orchestration.loop import run_job
from minimise.storage.git_tracker import GitTracker
from pathlib import Path

db = Database(Path(r'{self.db.db_path}'))
git_tracker = GitTracker(Path(r'{self.repo_path}'))
jobs_dir = Path(r'{self.jobs_dir}')
manager = JobManager(db, git_tracker, jobs_dir, Path(r'{self.repo_path}'))
run_job(manager, '{job_id}')
"""
            process = subprocess.Popen(
                ["python", "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            pid = process.pid
            self.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow(), pid=pid)
            if self.on_job_update:
                self.on_job_update(job_id)
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

        self.db.update_job_status(job_id, JobStatus.STOPPED, completed_at=datetime.utcnow())
        tasks = self.db.list_tasks_for_job(job_id)
        for task in tasks:
            if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                self.db.update_task_status(task.id, TaskStatus.STOPPED, completed_at=datetime.utcnow())
                if self.on_task_update:
                    self.on_task_update(job_id, task.id)

        if self.on_job_update:
            self.on_job_update(job_id)

        return True
