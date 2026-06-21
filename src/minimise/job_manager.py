"""Job Manager for orchestrating plan execution and task sequencing."""

import uuid
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional

from minimise.models import Job, Task, JobStatus, TaskStatus
from minimise.database import Database
from minimise.git_tracker import GitTracker
from minimise.task_executor import TaskExecutor
from minimise.handover_manager import HandoverManager
from minimise.utils import ensure_directory


class JobManager:
    """Orchestrates plan execution, task sequencing, and handover between tasks."""

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, repo_path: Path):
        """
        Initialize JobManager.

        Args:
            db: Database instance for storing jobs and tasks
            git_tracker: GitTracker instance for managing git state
            jobs_dir: Directory to store job artifacts
            repo_path: Path to the git repository
        """
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = ensure_directory(jobs_dir)
        self.repo_path = Path(repo_path)
        self.task_executor = TaskExecutor(db, git_tracker, jobs_dir)

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
            with open(plan_path, "r") as f:
                plan_config = yaml.safe_load(f)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return None

        # Extract plan metadata
        plan_name = plan_config.get("name", "Unnamed Plan")
        plan_briefing = plan_config.get("briefing", "")
        pre_plan_hook = plan_config.get("pre_plan_hook", "")
        post_plan_hook = plan_config.get("post_plan_hook", "")
        tasks_config = plan_config.get("tasks", [])

        # Create Job object
        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            name=plan_name,
            status=JobStatus.PENDING,
            plan_path=str(plan_path),
            base_commit=base_commit,
            created_at=datetime.utcnow(),
        )

        # Create Task objects from config
        tasks = []
        for idx, task_config in enumerate(tasks_config):
            task_id = str(uuid.uuid4())
            task = Task(
                id=task_id,
                job_id=job_id,
                name=task_config.get("name", f"Task {idx + 1}"),
                description=task_config.get("description", ""),
                status=TaskStatus.PENDING,
                created_at=datetime.utcnow(),
            )
            tasks.append(task)

        job.tasks = tasks

        # Store job and tasks in database
        self.db.create_job(job)
        for task in tasks:
            self.db.create_task(task)

        # Store plan copy in jobs directory
        job_dir = ensure_directory(self.jobs_dir / job_id)
        plan_copy_path = job_dir / "plan.yaml"
        with open(plan_copy_path, "w") as f:
            yaml.dump(plan_config, f)

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
            with open(plan_path, "r") as f:
                plan_config = yaml.safe_load(f)
        except Exception as e:
            print(f"Error reading plan file: {e}")
            return False

        # Update job status to RUNNING
        self.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())

        # Load hooks from disk
        pre_plan_hook = (job_dir / "pre_plan_hook.txt").read_text() if (job_dir / "pre_plan_hook.txt").exists() else ""
        post_plan_hook = (job_dir / "post_plan_hook.txt").read_text() if (job_dir / "post_plan_hook.txt").exists() else ""

        # Run pre_plan_hook
        if pre_plan_hook:
            from minimise.utils import run_shell_command
            success, output = run_shell_command(pre_plan_hook)
            if not success:
                print(f"Pre-plan hook failed: {output}")
                self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
                return False

        # Load all tasks for this job from database
        tasks = self.db.list_tasks_for_job(job_id)
        tasks_config = plan_config.get("tasks", [])

        # Map tasks to their config by index for hooks
        handover_context = ""

        for idx, task in enumerate(tasks):
            # Get task config for hooks
            task_config = tasks_config[idx] if idx < len(tasks_config) else {}
            pre_task_hook = task_config.get("pre_task_hook", "")
            post_task_hook = task_config.get("post_task_hook", "")

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
                self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
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
                self.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
                return False

        # Mark job as COMPLETED
        self.db.update_job_status(job_id, JobStatus.COMPLETED, completed_at=datetime.utcnow())

        return True

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job.

        Stub implementation for now - marks job as cancelled.

        Args:
            job_id: ID of the job to cancel

        Returns:
            True if cancel was successful, False otherwise
        """
        job = self.db.get_job(job_id)
        if not job:
            return False

        # For now, just return False (stub)
        return False

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
