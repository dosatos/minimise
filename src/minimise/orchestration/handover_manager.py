"""Handover context manager for passing context between sequential tasks."""

from minimise.models import Task


class HandoverManager:
    """Builds handover context passed from one task to the next."""

    @staticmethod
    def build_handover_prompt(
        current_task_output: str, diff: str, next_task: Task
    ) -> str:
        """
        Build a handover prompt combining previous task output, diff, and next task context.

        Args:
            current_task_output: Output from the completed task
            diff: Git diff since job start
            next_task: The next task to execute

        Returns:
            A formatted prompt for the next agent
        """
        # Extract file and line change summary from diff
        file_count = diff.count("diff --git")
        # Count actual code changes, excluding diff metadata lines (+++, ---)
        lines_added = len([l for l in diff.split("\n") if l.startswith("+") and not l.startswith("+++")])
        lines_removed = len([l for l in diff.split("\n") if l.startswith("-") and not l.startswith("---")])

        # Truncate diff to 2000 characters for token efficiency
        truncated_diff = diff[:2000]
        if len(diff) > 2000:
            truncated_diff += "..."

        prompt = f"""## Previous Task Summary

**Task Output:**
{current_task_output}

**Changes Made:**
- Files changed: {file_count}
- Lines added: {lines_added}
- Lines removed: {lines_removed}

**Diff Summary:**
```diff
{truncated_diff}
```

## Next Task

**Name:** {next_task.name}
**Description:** {next_task.description}

Please continue from where the previous task left off and complete this task."""

        return prompt

    @staticmethod
    def build_retry_prompt(prior_handover: str, task: Task, attempt: int, error_output: str) -> str:
        """Build the context for re-attempting a failed task: prior handover + what went wrong.

        This is the "learn from the failure" signal — the failed attempt's report is
        injected into the next attempt so the agent can avoid repeating it.
        ponytail: failure report is just the error output for now; richer capture
        (diff, conversation, learnings) is the separate handover-quality effort.
        """
        return f"""{prior_handover}

## Previous Attempt Failed (attempt {attempt})

The previous attempt at this task did not succeed. Learn from what went wrong
and take a different approach.

**Task:** {task.name}

**Failure output:**
{error_output}

Please complete the task, avoiding the failure above."""
