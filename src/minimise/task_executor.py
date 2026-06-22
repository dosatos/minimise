import os
from datetime import datetime
from pathlib import Path
from minimise.models import Task, TaskStatus
from minimise.database import Database
from minimise.git_tracker import GitTracker
from minimise.utils import run_shell_command, ensure_directory


class TaskExecutor:
    """Executes individual tasks with retry logic and hooks."""

    MAX_RETRIES = 3

    def __init__(self, db: Database, git_tracker: GitTracker, jobs_dir: Path, on_task_update=None):
        self.db = db
        self.git_tracker = git_tracker
        self.jobs_dir = jobs_dir
        self.on_task_update = on_task_update

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
                # Issue #1 fix: Update status to FAILED before returning
                self.db.update_task_status(
                    task.id,
                    TaskStatus.FAILED,
                    output=f"Post-task hook failed: {hook_output}",
                    retries=task.retries,
                    completed_at=datetime.utcnow(),
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

            self.db.update_task_status(
                task.id,
                TaskStatus.COMPLETED,
                output=final_output,
                retries=task.retries,
                completed_at=datetime.utcnow(),
            )
        else:
            self.db.update_task_status(
                task.id,
                TaskStatus.FAILED,
                output=final_output,
                retries=task.retries,
                completed_at=datetime.utcnow(),
            )

        return final_success, final_output

    def _build_claude_env(self) -> dict:
        """Build secure environment for Claude Code subprocess.

        Only includes necessary env vars for Claude auth and PATH resolution.
        Excludes sensitive credentials and unnecessary variables.
        Uses either Anthropic API OR AWS Bedrock, never both (to avoid conflicts).
        """
        use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1"

        # Common vars for all backends
        common_keys = {
            "PATH",           # Required to find claude command
            "HOME",           # Required for ~/.claude auth cache
            "USER",           # Context info
            "SHELL",          # Shell preferences
            "LANG",           # Locale
        }

        if use_bedrock:
            # Use only Bedrock credentials
            safe_keys = common_keys | {
                "CLAUDE_CODE_USE_BEDROCK",
                "AWS_REGION",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_BEARER_TOKEN_BEDROCK",
            }
        else:
            # Use only Anthropic credentials
            safe_keys = common_keys | {"ANTHROPIC_API_KEY"}

        return {k: v for k, v in os.environ.items() if k in safe_keys}

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
        import subprocess

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

Execute this task by modifying the codebase as needed. When done, write a summary of what you implemented."""

        try:
            # Spawn Claude Code CLI with the prompt via stdin
            # Use: claude -p --output-format text --dangerously-skip-permissions
            # The -p flag enables non-interactive mode, reading prompt from stdin
            # --dangerously-skip-permissions allows auto-approval in background job context
            result = subprocess.run(
                ["claude", "-p", "--output-format", "text", "--dangerously-skip-permissions"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.jobs_dir.parent.parent),  # Run from repo root
                env=self._build_claude_env(),  # Pass only necessary env vars
            )

            output = result.stdout
            success = result.returncode == 0

            return success, output

        except subprocess.TimeoutExpired:
            return False, f"Task execution timeout after 300 seconds"
        except Exception as e:
            return False, f"Failed to invoke Claude Code: {str(e)}"
