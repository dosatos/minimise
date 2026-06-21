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
        lines_added = len([l for l in diff.split("\n") if l.startswith("+")])
        lines_removed = len([l for l in diff.split("\n") if l.startswith("-")])

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
