"""HookExecutor — runs plan-level lifecycle hooks (pre-plan / post-plan).

At par with TaskExecutor and JobExecutor, but deliberately pure: it runs a
named hook and reports success. Failing the job (marking it FAILED in the
store) is the run loop's concern, so it lives on JobExecutor, not here.
"""

from minimise.utils import run_shell_command


class HookExecutor:
    """Runs a named plan-level hook; returns whether it succeeded."""

    def run(self, command: str, label: str) -> bool:
        """Run the hook; log and return False if it errors (empty hook succeeds)."""
        if not command:
            return True
        success, output = run_shell_command(command)
        if not success:
            print(f"{label} hook failed: {output}")
        return success


def demo():
    assert HookExecutor().run("", "Pre-plan") is True
    assert HookExecutor().run("exit 0", "Pre-plan") is True
    assert HookExecutor().run("exit 1", "Post-plan") is False
    print("OK")


if __name__ == "__main__":
    demo()
