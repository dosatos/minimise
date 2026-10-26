"""JobExecutor — runs a job's tasks sequentially, end to end.

Pure orchestration: run plan hooks, execute each task in order while flowing
a handover report to the next, and report success. It writes no storage —
JobController loads the job/plan and marks RUNNING/COMPLETED/FAILED around
this call; per-task persistence lives in TaskExecutor. A task that hits the
agent's usage limit raises JobStopped, which ends the run before the next task.
So does a job marked STOPPED (`mini job stop`): its status is re-read before
every task and every hook, so an engine that outlives its stop goes no further.
"""

from minimise.models import Job, JobStatus, Plan, TaskStatus
from minimise.orchestration.task_executor import JobStopped, TaskExecutor
from minimise.orchestration.hook_executor import HookExecutor
from minimise.utils import dump_yaml_readable


class JobExecutor:
    """Runs all of a job's tasks sequentially."""

    def __init__(self, task_executor: TaskExecutor, hook_executor: HookExecutor):
        self.task_executor = task_executor
        self.hook_executor = hook_executor

    def _raise_if_stopped(self, job_id) -> None:
        """End the run (JobStopped) if the job was marked STOPPED meanwhile."""
        job = self.task_executor.store.load(job_id)
        if job is not None and job.status == JobStatus.STOPPED:
            raise JobStopped("Job stopped")

    def _run_hooks(self, job_id, hooks, execution_type, task_id, stdin=None) -> bool:
        for hook in hooks:
            self._raise_if_stopped(job_id)
            ok, _ = self.hook_executor.run(hook, execution_type, task_id, stdin=stdin)
            if not ok:
                print(f"{execution_type} hook '{hook.name}' failed")
                return False
        return True

    def _make_post_verify(self, job_id, hooks, task_id, stdin):
        """Closure run after each successful attempt: run post_task hooks in
        order, mapping on_failure to (outcome, combined_output). JobStopped
        raised here propagates through TaskExecutor.execute_task untouched."""
        def verify(attempt):
            combined = ""
            for hook in hooks:
                self._raise_if_stopped(job_id)
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
        # This is the complete, stable plan context available to task prompts;
        # the renderer omits it when briefing is blank. Hooks still receive the
        # full serialized plan through stdin.
        plan_name = plan.name
        plan_briefing = plan.briefing
        plan_yaml = dump_yaml_readable(plan.model_dump())

        if not self._run_hooks(job.id, plan.pre_hooks, "pre_plan", None, stdin=plan_yaml):
            return False

        handover = ""
        resuming = True  # until we execute the first non-complete task
        for idx, task in enumerate(job.tasks):
            # Resume: skip already-completed tasks, and seed the first executed
            # task with the previous (completed) task's persisted handoff.
            if task.status == TaskStatus.COMPLETED:
                continue
            self._raise_if_stopped(job.id)
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
            # timeout_min lives in the plan, not the tasks table — carry it over
            # each run so a resumed job honours the current plan's timeout.
            task.timeout_min = getattr(plan_task, "timeout_min", None)
            pre = getattr(plan_task, "pre_hooks", []) if plan_task else []
            post = getattr(plan_task, "post_hooks", []) if plan_task else []

            if not self._run_hooks(job.id, pre, "pre_task", task.id, stdin=plan_yaml):
                self.task_executor.store.mark_task_failed(task, "Pre-task hook failed")
                return False

            success, output = self.task_executor.execute_task(
                task, job.id, handover, next_task=next_task,
                verify=self._make_post_verify(job.id, post, task.id, plan_yaml),
                plan_name=plan_name, plan_briefing=plan_briefing,
            )
            if not success:
                print(f"Task {task.name} failed: {output}")
                return False

            # execute_task returns the completed task's handoff for the next one.
            handover = output

        return self._run_hooks(job.id, plan.post_hooks, "post_plan", None, stdin=plan_yaml)
