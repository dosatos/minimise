"""Job Manager for orchestrating plan execution and task sequencing."""

import uuid
import yaml
import subprocess
import signal
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from minimise.models import Job, Task, JobStatus, TaskStatus, Plan
from minimise.database import Database
from minimise.git_tracker import GitTracker
from minimise.task_executor import TaskExecutor
from minimise.handover_manager import HandoverManager
from minimise.utils import ensure_directory


class JobManager:
    """Orchestrates plan execution, task sequencing, and handover between tasks."""

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, repo_path: Path, on_job_update=None, on_task_update=None):
        """
        Initialize JobManager.

        Args:
            db: Database instance for storing jobs and tasks
            git_tracker: GitTracker instance for managing git state
            jobs_dir: Directory to store job artifacts
            repo_path: Path to the git repository
            on_job_update: Optional callback for job status updates (job_id)
            on_task_update: Optional callback for task status updates (job_id, task_id)
        """
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = ensure_directory(jobs_dir)
        self.repo_path = Path(repo_path)
        self.task_executor = TaskExecutor(db, git_tracker, jobs_dir)
        self.on_job_update = on_job_update
        self.on_task_update = on_task_update

    def release_lock(self, plan_path: str) -> None:
        """
        Release any file locks held by a plan.

        Args:
            plan_path: Path to the plan (used to derive lock file path)
        """
        plan_path_obj = Path(plan_path)
        lock_path = plan_path_obj.parent / f"{plan_path_obj.stem}.lock"
        if lock_path.exists():
            try:
                lock_path.unlink()
            except Exception as e:
                print(f"Warning: Could not release lock at {lock_path}: {e}")

    def create_job(self, plan_path: Path) -> Optional[Job]:
        """
        Create a job from a plan.yaml file.

        Validates git clean state, parses the plan, creates Job and Task objects,
        records base_commit, and stores plan copy in jobs directory.

        Args:
            plan_path: Path to the plan.yaml file

        Returns:
            Job object with tasks, or None if creation failed
        """
        # Validate git clean state
        is_clean, message = self.git_tracker.validate_clean_state()
        if not is_clean:
            print(f"Error: {message}")
            return None

        # Get current commit as base
        base_commit = self.git_tracker.get_current_commit()
        if not base_commit:
            print("Error: Could not get current commit")
            return None

        # Parse plan.yaml
        try:
            plan = Plan.from_yaml(plan_path)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return None

        # Hooks are pydantic extras (extra="allow").
        pre_plan_hook = getattr(plan, "pre_plan_hook", "")
        post_plan_hook = getattr(plan, "post_plan_hook", "")

        # Create Job object
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name=plan.name,
            status=JobStatus.PENDING,
            plan_path=str(plan_path),
            base_commit=base_commit,
            created_at=datetime.utcnow(),
        )

        # Create Task objects (pydantic guarantees typed, present fields)
        tasks = [
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

        job.tasks = tasks

        # Store job and tasks in database
        self.db.create_job(job)
        for task in tasks:
            self.db.create_task(task)

        # Store plan copy in jobs directory
        job_dir = ensure_directory(self.jobs_dir / job_id)
        plan_copy_path = job_dir / "plan.yaml"
        with open(plan_copy_path, "w") as f:
            yaml.dump(plan.model_dump(), f)

        # Store plan metadata for later retrieval
        (job_dir / "base_commit.txt").write_text(base_commit)
        (job_dir / "pre_plan_hook.txt").write_text(pre_plan_hook)
        (job_dir / "post_plan_hook.txt").write_text(post_plan_hook)

        return job

    def run_job(self, job_id: str) -> bool:
        """
        Execute entire job (all tasks sequentially).

        Loads job and plan, runs pre_plan_hook, executes tasks sequentially with
        handover context flowing between them, runs post_plan_hook, and marks
        job as completed or failed.

        Args:
            job_id: ID of the job to run

        Returns:
            True if job completed successfully, False otherwise
        """
        # Load job from database
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        # Load plan from disk
        job_dir = self.jobs_dir / job_id
        plan_path = job_dir / "plan.yaml"
        if not plan_path.exists():
            print(f"Plan file not found for job {job_id}")
            return False

        try:
            plan = Plan.from_yaml(plan_path)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return False

        # Update job status to RUNNING
        self.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())
        if self.on_job_update:
            self.on_job_update(job_id)

        # Load hooks from disk
        pre_plan_hook = (job_dir / "pre_plan_hook.txt").read_text() if (job_dir / "pre_plan_hook.txt").exists() else ""
        post_plan_hook = (job_dir / "post_plan_hook.txt").read_text() if (job_dir / "post_plan_hook.txt").exists() else ""

        # Run pre_plan_hook
        if pre_plan_hook:
            from minimise.utils import run_shell_command
            success, output = run_shell_command(pre_plan_hook)
            if not success:
                print(f"Pre-plan hook failed: {output}")
                self.release_lock(str(plan_path))
                self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
                if self.on_job_update:
                    self.on_job_update(job_id)
                return False

        # Load all tasks for this job from database
        tasks = self.db.list_tasks_for_job(job_id)

        # Map tasks to their config by index for hooks
        handover_context = ""

        for idx, task in enumerate(tasks):
            # Per-task hooks live as pydantic extras on the plan task (by index)
            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            pre_task_hook = getattr(plan_task, "pre_task_hook", "") or ""
            post_task_hook = getattr(plan_task, "post_task_hook", "") or ""

            # Execute task
            success, output = self.task_executor.execute_task(
                task,
                job_id,
                handover_context,
                pre_task_hook=pre_task_hook,
                post_task_hook=post_task_hook,
            )

            if not success:
                print(f"Task {task.name} failed: {output}")
                self.release_lock(str(plan_path))
                self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
                if self.on_job_update:
                    self.on_job_update(job_id)
                return False

            # Build handover context for next task
            if job.base_commit:
                diff = self.git_tracker.get_diff(job.base_commit)
            else:
                diff = ""

            # Build handover prompt if there are more tasks
            if idx < len(tasks) - 1:
                next_task = tasks[idx + 1]
                handover_context = HandoverManager.build_handover_prompt(output, diff, next_task)

        # Run post_plan_hook
        if post_plan_hook:
            from minimise.utils import run_shell_command
            success, output = run_shell_command(post_plan_hook)
            if not success:
                print(f"Post-plan hook failed: {output}")
                self.release_lock(str(plan_path))
                self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
                if self.on_job_update:
                    self.on_job_update(job_id)
                return False

        # Mark job as COMPLETED
        self.db.update_job_status(job_id, JobStatus.COMPLETED, completed_at=datetime.utcnow())
        if self.on_job_update:
            self.on_job_update(job_id)

        return True

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job.

        Updates job status to STOPPED and marks all PENDING/RUNNING tasks as stopped.

        Args:
            job_id: ID of the job to cancel

        Returns:
            True if cancel was successful, False otherwise
        """
        job = self.db.get_job(job_id)
        if not job:
            return False

        # Update job status to STOPPED
        self.db.update_job_status(job_id, JobStatus.STOPPED, completed_at=datetime.utcnow())
        if self.on_job_update:
            self.on_job_update(job_id)

        # Cancel all tasks that are not already completed/failed
        tasks = self.db.list_tasks_for_job(job_id)
        for task in tasks:
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                self.db.update_task_status(task.id, TaskStatus.STOPPED, completed_at=datetime.utcnow())
                if self.on_task_update:
                    self.on_task_update(job_id, task.id)

        return True

    def get_job_status(self, job_id: str) -> Optional[Job]:
        """
        Get job status with all associated tasks.

        Args:
            job_id: ID of the job to retrieve

        Returns:
            Job object with all tasks, or None if not found
        """
        job = self.db.get_job(job_id)
        if not job:
            return None

        # Load all tasks for this job
        tasks = self.db.list_tasks_for_job(job_id)
        job.tasks = tasks

        return job

    def start_job(self, job_id: str) -> Optional[int]:
        """
        Start a job by spawning it as a subprocess in the background.

        Only works if job status is PENDING. Spawns a subprocess that runs run_job
        and returns the PID immediately (non-blocking).

        Args:
            job_id: ID of the job to start

        Returns:
            Process PID if job started successfully, None otherwise
        """
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return None

        if job.status != JobStatus.PENDING:
            print(f"Job must be in PENDING state (current: {job.status.value})")
            return None

        try:
            # Create a Python script that will run the job in a subprocess
            # Use start_new_session=True to create a new process group
            script = f"""
import sys
sys.path.insert(0, '{str(self.jobs_dir.parent.parent)}')
from minimise.database import Database
from minimise.job_manager import JobManager
from minimise.git_tracker import GitTracker
from pathlib import Path

db = Database(Path.home() / '.minimise' / 'minimise.db')
git_tracker = GitTracker(Path.cwd())
jobs_dir = Path.home() / '.minimise' / 'jobs'
manager = JobManager(db, git_tracker, jobs_dir, Path.cwd())
manager.run_job('{job_id}')
"""
            process = subprocess.Popen(
                ["python", "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            pid = process.pid

            # Update job status to RUNNING with PID
            self.db.update_job_status(
                job_id,
                JobStatus.RUNNING,
                started_at=datetime.utcnow(),
                pid=pid
            )
            if self.on_job_update:
                self.on_job_update(job_id)

            return pid

        except Exception as e:
            print(f"Error starting job: {e}")
            return None

    def stop_job(self, job_id: str) -> bool:
        """
        Stop a running job by sending SIGTERM to its subprocess.

        Only works if job status is RUNNING and has an associated PID.
        Sets job status to STOPPED and updates running/pending tasks to STOPPED.

        Args:
            job_id: ID of the job to stop

        Returns:
            True if job stopped successfully, False otherwise
        """
        job = self.db.get_job(job_id)
        if not job:
            print(f"Job {job_id} not found")
            return False

        if job.status != JobStatus.RUNNING:
            print(f"Job must be in RUNNING state (current: {job.status.value})")
            return False

        if job.pid is None:
            print("Job has no associated process")
            return False

        try:
            # Send SIGTERM to the process group
            os.killpg(os.getpgid(job.pid), signal.SIGTERM)
        except ProcessLookupError:
            # Process already terminated; still finalize state below.
            pass
        except Exception as e:
            print(f"Error stopping job: {e}")
            return False

        # Update job status to STOPPED and mark running/pending tasks STOPPED.
        self.db.update_job_status(
            job_id,
            JobStatus.STOPPED,
            completed_at=datetime.utcnow()
        )
        tasks = self.db.list_tasks_for_job(job_id)
        for task in tasks:
            if task.status in [TaskStatus.RUNNING, TaskStatus.PENDING]:
                self.db.update_task_status(task.id, TaskStatus.STOPPED)

        if self.on_job_update:
            self.on_job_update(job_id)

        return True
