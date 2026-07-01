"""JobExecutor — runs a job's tasks sequentially, end to end.

Pure orchestration: run plan hooks, execute each task in order while flowing
a handover report to the next, and report success. It touches no storage —
JobController loads the job/plan and marks RUNNING/COMPLETED/FAILED around
this call; per-task persistence lives in TaskExecutor.
"""

import yaml

from minimise.models import Job, Plan
from minimise.orchestration.task_executor import TaskExecutor
from minimise.orchestration.hook_executor import HookExecutor


class JobExecutor:
    """Runs all of a job's tasks sequentially."""

    def __init__(self, task_executor: TaskExecutor, hook_executor: HookExecutor):
        self.task_executor = task_executor
        self.hook_executor = hook_executor

    def _run_hooks(self, hooks, execution_type, task_id, stdin=None) -> bool:
        for hook in hooks:
            if not self.hook_executor.run(hook, execution_type, task_id, stdin=stdin):
                print(f"{execution_type} hook '{hook.name}' failed")
                return False
        return True

    def execute(self, job: Job, plan: Plan) -> bool:
        """Run all of a job's tasks (and plan hooks); returns True on success."""
        plan_yaml = yaml.dump(plan.model_dump())

        if not self._run_hooks(plan.pre_hooks, "pre_plan", None, stdin=plan_yaml):
            return False

        handover = ""
        for idx, task in enumerate(job.tasks):
            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            next_task = job.tasks[idx + 1] if idx < len(job.tasks) - 1 else None
            pre = getattr(plan_task, "pre_hooks", []) if plan_task else []
            post = getattr(plan_task, "post_hooks", []) if plan_task else []

            if not self._run_hooks(pre, "pre_task", task.id, stdin=plan_yaml):
                self.task_executor.store.mark_task_failed(task, "Pre-task hook failed")
                return False

            success, output = self.task_executor.execute_task(
                task, job.id, handover, next_task=next_task,
            )
            if not success:
                print(f"Task {task.name} failed: {output}")
                return False

            if not self._run_hooks(post, "post_task", task.id, stdin=plan_yaml):
                self.task_executor.store.mark_task_failed(task, "Post-task hook failed")
                return False

            # execute_task returns the completed task's handoff for the next one.
            handover = output

        return self._run_hooks(plan.post_hooks, "post_plan", None, stdin=plan_yaml)
