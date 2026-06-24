"""The job execution loop — runs a plan's tasks sequentially end to end.

This is the orchestration engine: given a created job, it loads the plan,
runs the pre/post-plan hooks, executes each task in order while flowing
handover context from one task to the next, and marks the job COMPLETED or
FAILED. The background subprocess spawned by ``JobManager.start_job`` calls
``run_job(manager, job_id)`` here directly.
"""

from datetime import datetime

from minimise.models import JobStatus, Plan
from minimise.orchestration.handover_manager import HandoverManager
from minimise.utils import run_shell_command


def _fail_job(manager, job_id):
    """Mark the job FAILED and notify."""
    manager.db.update_job_status(job_id, JobStatus.FAILED, completed_at=datetime.utcnow())
    if manager.on_job_update:
        manager.on_job_update(job_id)


def run_job(manager, job_id: str) -> bool:
    """Execute an entire job (all tasks sequentially).

    Loads job and plan, runs pre_plan_hook, executes tasks sequentially with
    handover context flowing between them, runs post_plan_hook, and marks the
    job as completed or failed.

    Args:
        manager: The JobManager owning the db, git_tracker, executor, callbacks.
        job_id: ID of the job to run.

    Returns:
        True if job completed successfully, False otherwise.
    """
    # Load job from database
    job = manager.db.get_job(job_id)
    if not job:
        print(f"Job {job_id} not found")
        return False

    # Load plan from disk
    job_dir = manager.jobs_dir / job_id
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
    manager.db.update_job_status(job_id, JobStatus.RUNNING, started_at=datetime.utcnow())
    if manager.on_job_update:
        manager.on_job_update(job_id)

    # Plan-level hooks live as pydantic extras on the parsed plan.
    pre_plan_hook = getattr(plan, "pre_plan_hook", "") or ""
    post_plan_hook = getattr(plan, "post_plan_hook", "") or ""

    # Run pre_plan_hook
    if pre_plan_hook:
        success, output = run_shell_command(pre_plan_hook)
        if not success:
            print(f"Pre-plan hook failed: {output}")
            _fail_job(manager, job_id)
            return False

    # Load all tasks for this job from database
    tasks = manager.db.list_tasks_for_job(job_id)

    # Execute tasks in order, flowing handover context between them
    handover_context = ""

    for idx, task in enumerate(tasks):
        # Per-task hooks live as pydantic extras on the plan task (by index)
        plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
        pre_task_hook = getattr(plan_task, "pre_task_hook", "") or ""
        post_task_hook = getattr(plan_task, "post_task_hook", "") or ""

        # Execute task
        success, output = manager.task_executor.execute_task(
            task,
            job_id,
            handover_context,
            pre_task_hook=pre_task_hook,
            post_task_hook=post_task_hook,
        )

        if not success:
            print(f"Task {task.name} failed: {output}")
            _fail_job(manager, job_id)
            return False

        # Build handover context for next task
        diff = manager.git_tracker.get_diff(job.base_commit) if job.base_commit else ""

        # Build handover prompt if there are more tasks
        if idx < len(tasks) - 1:
            next_task = tasks[idx + 1]
            handover_context = HandoverManager.build_handover_prompt(output, diff, next_task)

    # Run post_plan_hook
    if post_plan_hook:
        success, output = run_shell_command(post_plan_hook)
        if not success:
            print(f"Post-plan hook failed: {output}")
            _fail_job(manager, job_id)
            return False

    # Mark job as COMPLETED
    manager.db.update_job_status(job_id, JobStatus.COMPLETED, completed_at=datetime.utcnow())
    if manager.on_job_update:
        manager.on_job_update(job_id)

    return True
