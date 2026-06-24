"""Hook — a shell command attached to a plan or task lifecycle point."""

from minimise.utils import run_shell_command


class Hook:
    """A (possibly empty) shell command run at a lifecycle point.

    An empty command is a no-op that succeeds, so callers never special-case it.
    """

    def __init__(self, command: str = ""):
        self.command = command or ""

    def run(self) -> tuple[bool, str]:
        """Run the command; returns (True, "") for an empty hook."""
        if not self.command:
            return True, ""
        return run_shell_command(self.command)


def demo():
    assert Hook("").run() == (True, "")
    assert Hook("exit 0").run()[0] is True
    assert Hook("exit 1").run()[0] is False
    print("OK")


if __name__ == "__main__":
    demo()
