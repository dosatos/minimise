"""Job timeline model shared by the terminal Gantt and the web detail page.

A job's timeline is its plan walked in order — plan hooks, then per task
(pre_hooks -> attempts -> post_hooks), then plan post_hooks — with each
step's status and timing taken from its execution record.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from minimise.models import Plan, TaskStatus


@dataclass
class Step:
    """One Gantt row — a task attempt or a hook. Name/estimate from the plan,
    status/timing from the execution (PENDING when none)."""
    name: str
    estimate: Optional[int]
    status: TaskStatus
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    is_hook: bool = False
    exit_reason: str = ""
    assignee: str = ""
    phase: str = "task"  # execution_type: pre_plan / pre_task / task / post_task / post_plan
    task_id: Optional[str] = None
    attempt: Optional[int] = None  # 1-based; set only on task attempts that ran

    @property
    def label(self) -> str:
        """Name with the attempt suffix the job log uses for its `step` field."""
        return f"{self.name}  · try {self.attempt}" if self.attempt else self.name


def _match_hook(execs, execution_type, task_id, hook_name):
    return next((e for e in execs if e.execution_type == execution_type
                 and e.task_id == task_id and e.hook_name == hook_name), None)


def _hook_steps(hooks, execs, execution_type, task_id):
    steps = []
    for hook in hooks:
        ex = _match_hook(execs, execution_type, task_id, hook.name)
        steps.append(Step(
            name=hook.name, estimate=hook.estimated_duration_min,
            status=ex.status if ex else TaskStatus.PENDING,
            started_at=ex.started_at if ex else None,
            ended_at=ex.completed_at if ex else None,
            is_hook=True, phase=execution_type, task_id=task_id,
        ))
    return steps


def build_steps(plan: Plan, tasks: list, executions: list) -> list:
    """Assemble Gantt rows in plan order: plan.pre_hooks, then per task
    (pre_hooks -> attempts -> post_hooks), then plan.post_hooks."""
    steps = _hook_steps(plan.pre_hooks, executions, "pre_plan", None)
    for idx, ptask in enumerate(plan.tasks):
        task = tasks[idx] if idx < len(tasks) else None
        task_id = task.id if task else None
        steps += _hook_steps(ptask.pre_hooks, executions, "pre_task", task_id)

        attempts = sorted(
            (e for e in executions if e.execution_type == "task" and e.task_id == task_id),
            key=lambda e: e.attempt,
        )
        assignee = (task.assignee or "") if task else ""
        if attempts:
            for e in attempts:
                steps.append(Step(name=ptask.name, attempt=e.attempt + 1,
                                  estimate=ptask.estimated_duration_min, status=e.status,
                                  started_at=e.started_at, ended_at=e.completed_at,
                                  exit_reason=e.exit_reason or "", assignee=assignee,
                                  task_id=task_id))
        else:
            steps.append(Step(name=ptask.name, estimate=ptask.estimated_duration_min,
                              status=TaskStatus.PENDING, assignee=assignee,
                              task_id=task_id))

        steps += _hook_steps(ptask.post_hooks, executions, "post_task", task_id)
    steps += _hook_steps(plan.post_hooks, executions, "post_plan", None)
    return steps


def project_steps(steps, job_start, now):
    """Project the whole plan onto one shared timeline (seconds from job_start).

    Walks steps in order carrying a projected cursor so pending work chains
    after the last known end. Returns (placements, total_secs) where each
    placement is (start_off, actual_end_off, proj_end_off) in seconds. The
    timeline spans the projected end of the last step, so bars fill the full
    width regardless of when the job is viewed."""
    placements = []
    cursor = 0.0
    now_off = (now - job_start).total_seconds()
    # per-step start offset (None if not started yet), index-aligned with steps
    started_offs = [(s.started_at - job_start).total_seconds() if s.started_at
                    else None for s in steps]
    for i, step in enumerate(steps):
        est = (step.estimate or 0) * 60
        done = step.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED)
        if done and step.started_at and step.ended_at:
            start_off = (step.started_at - job_start).total_seconds()
            actual_end_off = proj_end_off = (step.ended_at - job_start).total_seconds()
        elif step.status == TaskStatus.RUNNING and step.started_at:
            start_off = (step.started_at - job_start).total_seconds()
            # a still-RUNNING step's solid bar must not paint past the start of
            # a later step that has already begun (e.g. its post_task hook)
            next_started = min((o for o in started_offs[i + 1:] if o is not None),
                               default=float("inf"))
            actual_end_off = min(now_off, next_started)
            proj_end_off = max(actual_end_off, start_off + est)
        else:  # PENDING (or no start time)
            start_off = cursor
            actual_end_off = start_off
            proj_end_off = start_off + est
        start_off = max(0, start_off)
        actual_end_off = max(0, actual_end_off)
        proj_end_off = max(0, proj_end_off)
        cursor = max(cursor, proj_end_off)
        placements.append((start_off, actual_end_off, proj_end_off))
    total_secs = max(cursor, 1)  # never divide by zero
    return placements, total_secs


def steps_from_executions(tasks: list, executions: list) -> list:
    """Fallback when the cached plan can't be parsed: executions in timeline
    order, then tasks that never started, in job order."""
    by_id = {t.id: t for t in tasks}
    steps, started = [], set()
    for ex in executions:
        task = by_id.get(ex.task_id)
        if ex.execution_type == "task":
            started.add(ex.task_id)
            steps.append(Step(name=task.name if task else ex.task_id or "",
                              attempt=ex.attempt + 1,
                              estimate=task.estimated_duration_min if task else None,
                              status=ex.status, started_at=ex.started_at,
                              ended_at=ex.completed_at, exit_reason=ex.exit_reason or "",
                              assignee=(task.assignee or "") if task else "",
                              task_id=ex.task_id))
        else:
            steps.append(Step(name=ex.hook_name or ex.execution_type, estimate=None,
                              status=ex.status, started_at=ex.started_at,
                              ended_at=ex.completed_at, is_hook=True,
                              phase=ex.execution_type, task_id=ex.task_id))
    for task in tasks:
        if task.id not in started:
            steps.append(Step(name=task.name, estimate=task.estimated_duration_min,
                              status=TaskStatus.PENDING, assignee=task.assignee or "",
                              task_id=task.id))
    return steps
