"""The job execution loop — runs a plan's tasks sequentially end to end.

This is the orchestration engine: load the job and plan, run the plan hooks,
execute each task in order while flowing a handover report from one task to
the next, and mark the job COMPLETED or FAILED. The background subprocess
spawned by ``JobExecutor.start_job`` calls ``run_job(executor, job_id)`` here.
"""

from minimise.orchestration.handover_manager import HandoverManager
from minimise.orchestration.hooks import Hook


def run_job(executor, job_id: str) -> bool:
    """Execute an entire job (all tasks sequentially); returns True on success.

    ``executor`` owns the store, git_tracker, task_executor, and callbacks.
    """
    store = executor.store
    job = store.load(job_id)
    if not job:
        print(f"Job {job_id} not found")
        return False

    try:
        plan = store.load_plan(job_id)
    except Exception as e:
        print(f"Error reading plan file: {e}")
        return False

    store.mark_job_running(job_id)
    executor.notify_job(job_id)

    pre_plan, post_plan = store.hooks(job_id)
    if not _run_hook(executor, job_id, Hook(pre_plan), "Pre-plan"):
        return False

    handover = ""
    for idx, task in enumerate(job.tasks):
        plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
        success, output = executor.task_executor.execute_task(
            task, job_id, handover,
            pre_task_hook=getattr(plan_task, "pre_task_hook", "") or "",
            post_task_hook=getattr(plan_task, "post_task_hook", "") or "",
        )
        if not success:
            print(f"Task {task.name} failed: {output}")
            return _fail_job(executor, job_id)

        # Hand the completed task's report to the next one.
        if idx < len(job.tasks) - 1:
            diff = executor.git_tracker.get_diff(job.base_commit) if job.base_commit else ""
            handover = HandoverManager.build_handover_prompt(output, diff, job.tasks[idx + 1])

    if not _run_hook(executor, job_id, Hook(post_plan), "Post-plan"):
        return False

    store.mark_job_completed(job_id)
    executor.notify_job(job_id)
    return True


def _run_hook(executor, job_id, hook: Hook, label: str) -> bool:
    """Run a plan-level hook; fail the job and return False if it errors."""
    success, output = hook.run()
    if not success:
        print(f"{label} hook failed: {output}")
        _fail_job(executor, job_id)
        return False
    return True


def _fail_job(executor, job_id) -> bool:
    """Mark the job FAILED, notify, and return False (the loop's failure result)."""
    executor.store.mark_job_failed(job_id)
    executor.notify_job(job_id)
    return False
