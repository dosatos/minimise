"""JobExecutor — runs a job's tasks sequentially, end to end.

Pure orchestration: run plan hooks, execute each task in order while flowing
a handover report to the next, and report success. It touches no storage —
JobController loads the job/plan and marks RUNNING/COMPLETED/FAILED around
this call; per-task persistence lives in TaskExecutor.
"""

import yaml

from minimise.models import Job, Plan, TaskStatus
from minimise.orchestration.task_executor import TaskExecutor
from minimise.orchestration.hook_executor import HookExecutor


class JobExecutor:
    """Runs all of a job's tasks sequentially."""

    def __init__(self, task_executor: TaskExecutor, hook_executor: HookExecutor):
        self.task_executor = task_executor
        self.hook_executor = hook_executor

    def _run_hooks(self, hooks, execution_type, task_id, stdin=None) -> bool:
        for hook in hooks:
            ok, _ = self.hook_executor.run(hook, execution_type, task_id, stdin=stdin)
            if not ok:
                print(f"{execution_type} hook '{hook.name}' failed")
                return False
        return True

    def _make_post_verify(self, hooks, task_id, stdin):
        """Closure run after each successful attempt: run post_task hooks in
        order, mapping on_failure to (outcome, combined_output)."""
        def verify(attempt):
            combined = ""
            for hook in hooks:
                ok, output = self.hook_executor.run(hook, "post_task", task_id, stdin=stdin)
                if ok:
                    continue
                combined += f"### {hook.name}\n{output}\n"
                if hook.on_failure == "skip":
                    continue  # recorded, non-blocking
                return hook.on_failure, combined  # "retry" or "fail"
            return "ok", combined
        return verify

    def execute(self, job: Job, plan: Plan) -> bool:
        """Run all of a job's tasks (and plan hooks); returns True on success."""
        plan_yaml = yaml.dump(plan.model_dump())

        if not self._run_hooks(plan.pre_hooks, "pre_plan", None, stdin=plan_yaml):
            return False

        handover = ""
        resuming = True  # until we execute the first non-complete task
        for idx, task in enumerate(job.tasks):
            # Resume: skip already-completed tasks, and seed the first executed
            # task with the previous (completed) task's persisted handoff.
            if task.status == TaskStatus.COMPLETED:
                continue
            if resuming:
                resuming = False
                if idx > 0:
                    prev = job.tasks[idx - 1]
                    p = self.task_executor.store.handoff_path(
                        job.id, prev.id, prev.retries
                    )
                    # arch4-1 guarantees this file for tasks completed under the
                    # new code; a legacy/deleted handoff falls back to "" so
                    # resume recovers instead of crashing.
                    handover = p.read_text() if p.exists() else ""

            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            next_task = job.tasks[idx + 1] if idx < len(job.tasks) - 1 else None
            pre = getattr(plan_task, "pre_hooks", []) if plan_task else []
            post = getattr(plan_task, "post_hooks", []) if plan_task else []

            if not self._run_hooks(pre, "pre_task", task.id, stdin=plan_yaml):
                self.task_executor.store.mark_task_failed(task, "Pre-task hook failed")
                return False

            success, output = self.task_executor.execute_task(
                task, job.id, handover, next_task=next_task,
                verify=self._make_post_verify(post, task.id, plan_yaml),
            )
            if not success:
                print(f"Task {task.name} failed: {output}")
                return False

            # execute_task returns the completed task's handoff for the next one.
            handover = output

        return self._run_hooks(plan.post_hooks, "post_plan", None, stdin=plan_yaml)
