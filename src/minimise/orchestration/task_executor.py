from typing import Optional
from minimise.models import Execution, Task, TaskStatus
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.handover_manager import HandoverManager
from minimise.agents.harness import AgentHarness, ClaudeCodeHarness


class TaskExecutor:
    """Executes individual tasks with retry logic. Hooks run in JobExecutor."""

    MAX_RETRIES = 3

    def __init__(
        self,
        store: JobStore,
        git_tracker: GitTracker,
        harness: Optional[AgentHarness] = None,
    ):
        self.store = store
        self.git_tracker = git_tracker
        self.harness = harness or ClaudeCodeHarness()

    def execute_task(
        self,
        task: Task,
        job_id: str,
        handover_context: str,
        next_task: Optional[Task] = None,
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

        for attempt in range(self.MAX_RETRIES + 1):
            self.store.mark_running(task, attempt)

            # Identity now lives in each JSON log line, not a flat banner.
            ex = Execution(
                job_id=job_id, task_id=task.id, attempt=attempt, execution_type="task"
            )
            handoff_path = self.store.handoff_path(job_id, task.id, attempt)
            success, output = self._invoke_claude_code({
                "handover": context,
                "task_name": task.name,
                "task_description": task.description,
                "task_goal": task.goal,
                "handoff_path": str(handoff_path),
                "log_path": str(job_log_path),
                "log_fields": {"execution_id": ex.execution_id, "type": ex.execution_type},
            })
            final_output = output

            if success:
                final_success = True
                break
            if attempt < self.MAX_RETRIES:
                self.store.record_attempt(task, attempt, output)
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
            self.store.record_completed(task, final_output, diff, commit_sha=commit_sha)
            if next_task is not None:
                # Successful attempt's handoff becomes the next task's context.
                return True, self._read_handoff(
                    handoff_path,
                    lambda: HandoverManager.build_handover_prompt(final_output, diff, next_task),
                )
        else:
            self.store.mark_task_failed(task, final_output)

        return final_success, final_output

    @staticmethod
    def _read_handoff(path, fallback) -> str:
        """Agent-written handoff if the file is non-empty, else the diff-based fallback."""
        content = path.read_text().strip() if path.exists() else ""
        if content:
            return f"(agent-written handoff)\n\n{content}"
        return f"WARNING auto-generated from diff - not reviewed\n\n{fallback()}"

    def _invoke_claude_code(self, context: dict) -> tuple[bool, str]:
        """
        Invoke Claude Code agent to execute task.

        Spawns a Claude Code agent subprocess with task description and context.
        The agent modifies the codebase and returns success status.

        Args:
            context: Context dictionary with task_name, task_description, task_goal, handover

        Returns:
            (success, output)
        """
        task_name = context.get("task_name", "Task")
        task_description = context.get("task_description", "")
        task_goal = context.get("task_goal", "")
        handover = context.get("handover", "")
        handoff_path = context.get("handoff_path", "")
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

⚠️  CRITICAL: Do not create exploratory jobs with 'mini job new'. If you accidentally create any jobs (test plans, temporary explorations, etc.), delete them before finishing:
   mini job delete <job_id> --force

⚠️  COMMITS: If you create any git commits, do NOT add co-author trailers (no "Co-Authored-By:" lines, no "Generated with Claude Code" lines). Use a plain commit message only. Prefer to leave changes uncommitted — the orchestrator commits your work for you.

Execute this task by modifying the codebase as needed. When done, write a summary of what you implemented.{handoff_section}"""

        # Delegate to the injected harness. The harness owns env construction,
        # the subprocess invocation, timeout (default 900s / 15 min), and
        # error handling.
        repo_root = str(self.git_tracker.repo_path)
        result = self.harness.run(
            prompt, cwd=repo_root, allow_edits=True, log_path=log_path, log_fields=log_fields
        )
        return result.success, (
            result.output if result.success else (result.error or result.output)
        )
