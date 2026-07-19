from datetime import datetime
from typing import Optional
from minimise.models import Execution, Task, TaskStatus
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.handover_manager import HandoverManager
from minimise.logging.backend import JsonlLogBackend


class TaskExecutor:
    """Executes individual tasks with retry logic. Hooks run in JobExecutor."""

    MAX_RETRIES = 3

    def __init__(
        self,
        store: JobStore,
        git_tracker: GitTracker,
        *,
        factory: Optional["HarnessFactory"] = None,
        personas: Optional[dict] = None,
    ):
        from minimise.agents.harness import HarnessFactory

        self.store = store
        self.git_tracker = git_tracker
        self._factory = factory or HarnessFactory(personas or {})
        self.personas = personas or {}

    def execute_task(
        self,
        task: Task,
        job_id: str,
        handover_context: str,
        next_task: Optional[Task] = None,
        verify=None,
    ) -> tuple[bool, str]:
        """Execute a task with retries; returns (success, output_or_handover).

        Each attempt writes a per-attempt handoff file. A failed attempt's
        handoff feeds the next attempt; on success the returned handover is the
        completed attempt's handoff (agent-written, or the diff-based builder as
        a marked fallback when the agent wrote nothing). Hooks run in
        JobExecutor (via HookExecutor), not here.
        """
        if not self.store.load(job_id):
            return False, f"Job {job_id} not found"

        # Capture task's base_commit at start (if not already set)
        if not task.base_commit:
            self.store.set_task_base_commit(task, self.git_tracker.get_current_commit())

        final_success = False
        final_output = ""
        context = handover_context

        job_log_path = self.store.job_log_path(job_id)
        log_backend = JsonlLogBackend()

        def _log_failure(step_label, detail):
            """Write terminal failure detail to job.log — the sole narration store."""
            if detail:
                log_backend.record(
                    str(job_log_path),
                    {"type": "task", "step": step_label},
                    detail, level="error",
                )

        def _step_label(attempt):
            return task.name + (f"  · try {attempt + 1}" if attempt > 0 else "")

        def _stash_work(msg):
            """Terminal failure: park the attempt's uncommitted work in a stash so
            it's recoverable, and tell the user how. Never on a retry path — the
            next attempt needs that work."""
            if self.git_tracker.stash(f"minimise: failed {task.id} ({task.name})"):
                return f"{msg}\n\nUncommitted work from this attempt was stashed: git stash pop"
            return msg

        for attempt in range(self.MAX_RETRIES + 1):
            self.store.mark_running(task, attempt)

            # Identity now lives in each JSON log line, not a flat banner.
            ex = Execution(
                job_id=job_id, task_id=task.id, attempt=attempt, execution_type="task"
            )
            handoff_path = self.store.handoff_path(job_id, task.id, attempt)
            harness = self._factory.from_task(task)
            model = self._factory.resolve_model(task)
            persona = self.personas.get(task.assignee) if task.assignee else None
            success, output, exit_reason = self._invoke_agent(harness, {
                "handover": context,
                "task_name": task.name,
                "task_description": task.description,
                "task_goal": task.goal,
                "timeout_min": task.timeout_min,
                "handoff_path": str(handoff_path),
                "system_prompt": persona.system_prompt if persona else None,
                "model": model,
                "log_path": str(job_log_path),
                "log_fields": {
                    "execution_id": ex.execution_id,
                    "type": ex.execution_type,
                    "step": task.name + (f"  · try {attempt + 1}" if attempt > 0 else ""),
                },
            })
            agent_end = datetime.utcnow()  # honest end of the attempt's work, before gating hook
            final_output = output

            if success:
                # verify (post_task hooks) gates the commit. None => today's behavior.
                if verify is not None:
                    outcome, combined = verify(attempt)
                    if outcome == "fail":
                        msg = _stash_work(f"Post-task hook failed\n{combined}")
                        _log_failure(_step_label(attempt), msg)
                        self.store.mark_task_failed(task, msg, exit_reason="hook_failed", ended_at=agent_end)
                        return False, msg
                    if outcome == "retry":
                        if attempt >= self.MAX_RETRIES:
                            msg = _stash_work(f"Post-task hook failed (retries exhausted)\n{combined}")
                            _log_failure(_step_label(attempt), msg)
                            self.store.mark_task_failed(task, msg, exit_reason="hook_failed", ended_at=agent_end)
                            return False, msg
                        _log_failure(_step_label(attempt), combined)
                        # The agent exited success; this FAILED attempt is the gating
                        # hook demanding a retry — book the hook, not the agent.
                        self.store.record_attempt(task, attempt, combined, exit_reason="hook_retry", ended_at=agent_end)
                        # Agent succeeded and wrote its handoff, so build the next
                        # context unconditionally and PREPEND the review findings —
                        # can't rely on build_retry_prompt's empty-handoff fallback.
                        base_context = self._read_handoff(
                            handoff_path,
                            lambda: HandoverManager.build_retry_prompt(handover_context, task, attempt, output),
                        )
                        context = f"## Post-task review findings (fix these)\n\n{combined}\n\n{base_context}"
                        continue
                final_success = True
                break
            if attempt < self.MAX_RETRIES:
                _log_failure(_step_label(attempt), output)
                self.store.record_attempt(task, attempt, output, exit_reason=exit_reason, ended_at=agent_end)
                # learn-from-failure: feed this attempt's handoff into the next.
                context = self._read_handoff(
                    handoff_path,
                    lambda: HandoverManager.build_retry_prompt(handover_context, task, attempt, output),
                )

        if final_success:
            commit_sha = None
            try:
                commit_sha = self.git_tracker.commit(f"Task {task.id}: {task.name}")
            except Exception as e:
                # Log commit failure but don't fail the task
                final_output += f"\n[Note: Git commit failed: {str(e)}]"
            diff = self.git_tracker.get_diff(task.base_commit) if task.base_commit else ""
            # Read the return value BEFORE persisting the fallback — else the write
            # below would mask the empty-file case the caller must still see.
            chain_handover = self._read_handoff(
                handoff_path,
                lambda: HandoverManager.build_handover_prompt(final_output, diff, next_task or task),
            ) if next_task is not None else None
            # Completion invariant: every COMPLETED task (incl. the last) leaves a
            # non-empty handoff on disk BEFORE the DB marks it COMPLETED — else a
            # crash in the gap gives resume a COMPLETED task with no handoff file.
            win_path = self.store.handoff_path(job_id, task.id, task.retries)
            if not (win_path.exists() and win_path.read_text().strip()):
                win_path.write_text(
                    HandoverManager.build_handover_prompt(final_output, diff, next_task or task)
                )
            self.store.record_completed(task, "", diff, commit_sha=commit_sha, exit_reason="success", ended_at=agent_end)
            if chain_handover is not None:
                return True, chain_handover
        else:
            final_output = _stash_work(final_output)
            _log_failure(_step_label(task.retries), final_output)
            self.store.mark_task_failed(task, final_output, exit_reason=exit_reason, ended_at=agent_end)

        return final_success, final_output

    @staticmethod
    def _read_handoff(path, fallback) -> str:
        """Agent-written handoff if the file is non-empty, else the diff-based fallback."""
        content = path.read_text().strip() if path.exists() else ""
        if content:
            return f"(agent-written handoff)\n\n{content}"
        return f"WARNING auto-generated from diff - not reviewed\n\n{fallback()}"

    def _invoke_agent(self, harness, context: dict) -> tuple[bool, str, str]:
        """
        Delegate to the injected agent harness.

        Builds the task prompt, calls harness.wrap_prompt(), then delegates to
        harness.run(). The harness owns env construction, subprocess invocation,
        and error handling. Timeout is only applied when the plan opts in via
        timeout_min.

        Args:
            harness: Resolved AgentHarness instance.
            context: Context dictionary with task_name, task_description, task_goal, handover

        Returns:
            (success, output, exit_reason)
        """
        task_name = context.get("task_name", "Task")
        task_description = context.get("task_description", "")
        task_goal = context.get("task_goal", "")
        handover = context.get("handover", "")
        handoff_path = context.get("handoff_path", "")
        system_prompt = context.get("system_prompt")
        model = context.get("model")
        log_path = context.get("log_path")
        log_fields = context.get("log_fields")

        # Build prompt for Claude Code agent
        goal_section = f"Goal: {task_goal}\n\n" if task_goal else ""
        handoff_section = f"""

When done, write a handoff for the next task to this exact path: {handoff_path}
Use these section headers:
## What changed & why
## Gotchas
## Current state
## What the next task needs""" if handoff_path else ""
        prompt = f"""You are executing a task in a multi-agent plan execution system.

Task: {task_name}

{goal_section}Description:
{task_description}

Context from previous tasks:
{handover if handover else "(no prior context)"}

Execute this task by modifying the codebase as needed. When done, write a summary of what you implemented.{handoff_section}"""

        # Delegate to the resolved harness. The harness owns env construction,
        # the subprocess invocation, and error handling. No timeout unless the
        # plan opts in with timeout_min — an agent is killed only on request.
        timeout = context["timeout_min"] * 60 if context.get("timeout_min") else None
        repo_root = str(self.git_tracker.repo_path)
        prompt = harness.wrap_prompt(prompt)
        result = harness.run(
            prompt, cwd=repo_root, allow_edits=True,
            model=model, system_prompt=system_prompt,
            log_path=log_path, log_fields=log_fields,
            timeout=timeout,
        )
        return result.success, (
            result.output if result.success else (result.error or result.output)
        ), result.exit_reason
