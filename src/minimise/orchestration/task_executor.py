from pathlib import Path
from typing import Optional
from minimise.models import Task
from minimise.storage.database import Database
from minimise.storage.git_tracker import GitTracker
from minimise.storage.job_store import JobStore
from minimise.orchestration.handover_manager import HandoverManager
from minimise.agents.harness import AgentHarness, ClaudeCodeHarness
from minimise.utils import run_shell_command


class TaskExecutor:
    """Executes individual tasks with retry logic and hooks."""

    MAX_RETRIES = 3

    def __init__(
        self,
        db: Database,
        git_tracker: GitTracker,
        jobs_dir: Path,
        harness: Optional[AgentHarness] = None,
    ):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = jobs_dir
        self.store = JobStore(db, jobs_dir)
        self.harness = harness or ClaudeCodeHarness()

    def execute_task(
        self,
        task: Task,
        job_id: str,
        handover_context: str,
        pre_task_hook: str = "",
        post_task_hook: str = "",
    ) -> tuple[bool, str]:
        """Execute a task with retries and hooks; returns (success, output).

        On a failed attempt, the failure report is injected into the next
        attempt's context (learn-from-failure handover).
        """
        if not self.db.get_job(job_id):
            return False, f"Job {job_id} not found"

        # Capture task's base_commit at start (if not already set)
        if not task.base_commit:
            self.store.set_task_base_commit(task, self.git_tracker.get_current_commit())

        if pre_task_hook:
            success, output = run_shell_command(pre_task_hook)
            if not success:
                return False, f"Pre-task hook failed: {output}"

        final_success = False
        final_output = ""
        context = handover_context

        for attempt in range(self.MAX_RETRIES + 1):
            self.store.mark_running(task, attempt)

            success, output = self._invoke_claude_code({
                "handover": context,
                "task_name": task.name,
                "task_description": task.description,
                "task_goal": task.goal,
            })
            final_output = output

            if success:
                final_success = True
                break
            if attempt < self.MAX_RETRIES:
                self.store.record_attempt(task, attempt, output)
                # learn-from-failure: feed this failure into the next attempt.
                context = HandoverManager.build_retry_prompt(handover_context, task, attempt, output)

        if post_task_hook:
            hook_success, hook_output = run_shell_command(post_task_hook)
            if not hook_success:
                self.store.mark_task_failed(task, f"Post-task hook failed: {hook_output}")
                return False, f"Post-task hook failed: {hook_output}"

        if final_success:
            try:
                self.git_tracker.commit(f"Task {task.id}: {task.name}")
            except Exception as e:
                # Log commit failure but don't fail the task
                final_output += f"\n[Note: Git commit failed: {str(e)}]"
            diff = self.git_tracker.get_diff(task.base_commit) if task.base_commit else ""
            self.store.record_completed(task, final_output, diff)
        else:
            self.store.mark_task_failed(task, final_output)

        return final_success, final_output

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

        # Build prompt for Claude Code agent
        goal_section = f"Goal: {task_goal}\n\n" if task_goal else ""
        prompt = f"""You are executing a task in a multi-agent plan execution system.

Task: {task_name}

{goal_section}Description:
{task_description}

Context from previous tasks:
{handover if handover else "(no prior context)"}

⚠️  CRITICAL: Do not create exploratory jobs with 'mini job new'. If you accidentally create any jobs (test plans, temporary explorations, etc.), delete them before finishing:
   mini job delete <job_id> --force

⚠️  COMMITS: If you create any git commits, do NOT add co-author trailers (no "Co-Authored-By:" lines, no "Generated with Claude Code" lines). Use a plain commit message only. Prefer to leave changes uncommitted — the orchestrator commits your work for you.

Execute this task by modifying the codebase as needed. When done, write a summary of what you implemented."""

        # Delegate to the injected harness. The harness owns env construction,
        # the subprocess invocation, timeout (default 900s / 15 min), and
        # error handling.
        repo_root = str(self.jobs_dir.parent.parent)  # Run from repo root
        result = self.harness.run(prompt, cwd=repo_root, allow_edits=True)
        return result.success, (
            result.output if result.success else (result.error or result.output)
        )
