from datetime import datetime
from pathlib import Path
from typing import Optional
from minimise.models import Task, TaskStatus
from minimise.database import Database
from minimise.git_tracker import GitTracker
from minimise.harness import AgentHarness, ClaudeCodeHarness
from minimise.utils import run_shell_command, ensure_directory


class TaskExecutor:
    """Executes individual tasks with retry logic and hooks."""

    MAX_RETRIES = 3

    def __init__(
        self,
        db: Database,
        git_tracker: GitTracker,
        jobs_dir: Path,
        on_task_update=None,
        harness: Optional[AgentHarness] = None,
    ):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = jobs_dir
        self.on_task_update = on_task_update
        self.harness = harness or ClaudeCodeHarness()

    def execute_task(
        self,
        task: Task,
        job_id: str,
        handover_context: str,
        pre_task_hook: str = "",
        post_task_hook: str = "",
    ) -> tuple[bool, str]:
        """
        Execute a task with retries and hooks.

        Args:
            task: Task to execute
            job_id: ID of parent job
            handover_context: Context from previous task
            pre_task_hook: Shell command to run before task
            post_task_hook: Shell command to run after task

        Returns:
            (success, output)
        """
        task_dir = ensure_directory(self.jobs_dir / job_id / "tasks" / task.id)

        # Get job to fetch base_commit
        job = self.db.get_job(job_id)
        if not job:
            return False, f"Job {job_id} not found"

        # Capture task's base_commit at start (if not already set)
        if not task.base_commit:
            task.base_commit = self.git_tracker.get_current_commit()
            self.db.update_task(task)

        # Run pre-task hook
        if pre_task_hook:
            success, output = run_shell_command(pre_task_hook)
            if not success:
                return False, f"Pre-task hook failed: {output}"

        # Attempt task execution with retries
        final_success = False
        final_output = ""
        started_at = None

        for attempt in range(self.MAX_RETRIES + 1):
            task.retries = attempt
            if attempt == 0:
                started_at = datetime.utcnow()
            self.db.update_task_status(task.id, TaskStatus.RUNNING, started_at=started_at if attempt == 0 else None)

            # Build execution context
            context = {
                "handover": handover_context,
                "task_name": task.name,
                "task_description": task.description,
                "task_goal": task.goal,
            }

            # Invoke Claude Code with task context
            success, output = self._invoke_claude_code(context)
            final_output = output

            if success:
                final_success = True
                break
            elif attempt < self.MAX_RETRIES:
                # Log failure and retry
                self.db.update_task_status(
                    task.id, TaskStatus.PENDING, output=f"Attempt {attempt} failed: {output}"
                )

        # Run post-task hook
        if post_task_hook:
            hook_success, hook_output = run_shell_command(post_task_hook)
            if not hook_success:
                self._finalize_task(
                    task.id, TaskStatus.FAILED, f"Post-task hook failed: {hook_output}", task.retries
                )
                return False, f"Post-task hook failed: {hook_output}"

        # Commit changes with task-specific message (before calculating diff)
        if final_success:
            commit_message = f"Task {task.id}: {task.name}"
            try:
                commit_result = self.git_tracker.commit(commit_message)
            except Exception as e:
                # Log commit failure but don't fail the task
                final_output += f"\n[Note: Git commit failed: {str(e)}]"
                commit_result = None

            # Calculate and store diff against task's base_commit (after commit)
            if task.base_commit:
                diff = self.git_tracker.get_diff(task.base_commit)
                diff_path = task_dir / "diff.txt"
                diff_path.write_text(diff)
                # Update only diff_path in database (preserves all other fields)
                self.db.update_task_diff_path(task.id, str(diff_path))

            self._finalize_task(task.id, TaskStatus.COMPLETED, final_output, task.retries)
        else:
            self._finalize_task(task.id, TaskStatus.FAILED, final_output, task.retries)

        return final_success, final_output

    def _finalize_task(self, task_id: str, status: TaskStatus, output: str, retries: int) -> None:
        """Record a terminal task status with a completion timestamp."""
        self.db.update_task_status(
            task_id, status, output=output, retries=retries, completed_at=datetime.utcnow()
        )

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
