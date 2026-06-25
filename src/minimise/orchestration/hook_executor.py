"""HookExecutor — runs plan-level lifecycle hooks (pre-plan / post-plan).

At par with TaskExecutor and JobExecutor, but deliberately pure: it runs a
named hook and reports success. Failing the job (marking it FAILED in the
store) is the run loop's concern, so it lives on JobExecutor, not here.
"""

from datetime import datetime

from minimise.models import Execution, TaskStatus
from minimise.utils import run_shell_command


class HookExecutor:
    """Runs a named plan-level hook; returns whether it succeeded.

    With a ``store`` it also records one timed Execution per non-empty hook;
    without one it behaves exactly as before.
    """

    def __init__(self, store=None, job_id=None):
        self.store = store
        self.job_id = job_id

    def run(self, command: str, label: str) -> bool:
        """Run the hook; log and return False if it errors (empty hook succeeds)."""
        if not command:
            return True
        started_at = datetime.utcnow()
        success, output = run_shell_command(command)
        if not success:
            print(f"{label} hook failed: {output}")
        if self.store:
            self.store.save_execution(Execution(
                job_id=self.job_id, task_id=None,
                execution_type=label.lower().replace("-", "_"), attempt=0,
                status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
                started_at=started_at, completed_at=datetime.utcnow(),
            ))
        return success


def demo():
    assert HookExecutor().run("", "Pre-plan") is True
    assert HookExecutor().run("exit 0", "Pre-plan") is True
    assert HookExecutor().run("exit 1", "Post-plan") is False
    print("OK")


if __name__ == "__main__":
    demo()
