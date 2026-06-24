"""JobExecutor — runs a job's tasks sequentially, end to end.

Pure orchestration: run plan hooks, execute each task in order while flowing
a handover report to the next, and report success. It touches no storage —
JobController loads the job/plan and marks RUNNING/COMPLETED/FAILED around
this call; per-task persistence lives in TaskExecutor.
"""

from minimise.models import Job, Plan
from minimise.storage.git_tracker import GitTracker
from minimise.orchestration.task_executor import TaskExecutor
from minimise.orchestration.hook_executor import HookExecutor
from minimise.orchestration.handover_manager import HandoverManager


class JobExecutor:
    """Runs all of a job's tasks sequentially."""

    def __init__(self, task_executor: TaskExecutor, hook_executor: HookExecutor, git_tracker: GitTracker):
        self.task_executor = task_executor
        self.hook_executor = hook_executor
        self.git_tracker = git_tracker

    def execute(self, job: Job, plan: Plan) -> bool:
        """Run all of a job's tasks (and plan hooks); returns True on success."""
        if not self.hook_executor.run(plan.pre_plan_hook, "Pre-plan"):
            return False

        handover = ""
        for idx, task in enumerate(job.tasks):
            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            success, output = self.task_executor.execute_task(
                task, job.id, handover,
                pre_task_hook=getattr(plan_task, "pre_task_hook", "") or "",
                post_task_hook=getattr(plan_task, "post_task_hook", "") or "",
            )
            if not success:
                print(f"Task {task.name} failed: {output}")
                return False

            # Hand the completed task's report to the next one.
            if idx < len(job.tasks) - 1:
                diff = self.git_tracker.get_diff(job.base_commit) if job.base_commit else ""
                handover = HandoverManager.build_handover_prompt(output, diff, job.tasks[idx + 1])

        return self.hook_executor.run(plan.post_plan_hook, "Post-plan")
