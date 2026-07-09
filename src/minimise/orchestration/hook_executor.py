"""HookExecutor — runs a named, timed hook (plan- or task-level).

Runs the hook's command in the TARGET repo's environment (fixes BUG-4),
records one timed Execution, and streams a log line through the existing
JSONL backend so the run is queryable. Failing the job is the run loop's
concern (JobExecutor/TaskExecutor), not here.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from minimise.models import Execution, Hook, TaskStatus
from minimise.utils import project_env, run_shell_command


class HookExecutor:
    def __init__(self, store=None, job_id=None, repo_root: Optional[Path] = None,
                 log_path=None, backend=None):
        self.store = store
        self.job_id = job_id
        self.repo_root = Path(repo_root) if repo_root else None
        self.log_path = log_path
        self.backend = backend

    def run(self, hook: Hook, execution_type: str, task_id: Optional[str],
            stdin: Optional[str] = None) -> tuple[bool, str]:
        """Run one hook in the project env; record + log; return (success, output)."""
        started_at = datetime.utcnow()
        # Record RUNNING before the shell runs so the status table shows the hook
        # as running (a `claude -p` reviewer can take minutes). save_execution is
        # keyed on execution_id, so the post-run save below upserts this row in place.
        ex = Execution(
            job_id=self.job_id, task_id=task_id, execution_type=execution_type,
            attempt=0, hook_name=hook.name, status=TaskStatus.RUNNING,
            started_at=started_at,
        )
        if self.store:
            self.store.save_execution(ex)

        env = project_env(self.repo_root) if self.repo_root else None
        timeout = hook.timeout_min * 60 if hook.timeout_min else None
        success, output = run_shell_command(hook.shell, cwd=self.repo_root, env=env, stdin=stdin,
                                            timeout=timeout)

        ex.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        ex.completed_at = datetime.utcnow()
        if self.store:
            self.store.save_execution(ex)
        if self.log_path and self.backend:
            fields = {"execution_id": ex.execution_id, "type": execution_type, "step": hook.name}
            level = "error" if not success else "info"
            self.backend.record(
                self.log_path, fields,
                f"{hook.name} — {'ok' if success else 'failed'}",
                level=level,
            )
            for line in output.splitlines():
                if line.strip():
                    self.backend.record(self.log_path, fields, line, level=level)
        if not success:
            print(f"Hook '{hook.name}' ({execution_type}) failed: {output}")
        return success, output


def demo():
    assert HookExecutor().run(Hook(name="ok", shell="exit 0", estimated_duration_min=1), "post_task", "t1")[0] is True
    assert HookExecutor().run(Hook(name="bad", shell="exit 1", estimated_duration_min=1), "pre_plan", None)[0] is False
    print("OK")


if __name__ == "__main__":
    demo()
