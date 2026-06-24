"""JobExecutor — runs a job's tasks sequentially, end to end.

Owns the run loop only: load the job + plan, run plan hooks, execute each
task in order while flowing a handover report to the next, and mark the job
COMPLETED or FAILED. Lifecycle/process control lives on JobController;
per-task work lives in TaskExecutor; persistence lives in JobStore.
"""

from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.task_executor import TaskExecutor
from minimise.orchestration.handover_manager import HandoverManager
from minimise.orchestration.hooks import Hook


class JobExecutor:
    """Runs all of a job's tasks sequentially."""

    def __init__(self, store: JobStore, task_executor: TaskExecutor, git_tracker: GitTracker):
        self.store = store
        self.task_executor = task_executor
        self.git_tracker = git_tracker

    def execute(self, job_id: str) -> bool:
        """Execute an entire job (all tasks sequentially); returns True on success."""
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
        """Mark the job FAILED and return False (the loop's failure result)."""
        self.store.mark_job_failed(job_id)
        return False
