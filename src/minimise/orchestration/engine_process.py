"""Engine process control: verify a job/loop engine pid and stop its whole tree.

`mini job start` / `mini loop start` spawn the engine (``<argv0> job _run <id>``)
with start_new_session=True, so the engine leads its own process group, and the
agent and hook subprocesses it runs join that group. Stopping signals the group,
not just the engine pid, so no orphaned agent keeps editing the checkout.

A pid is only ever signalled after its command line proves it is THIS run's
engine: a dead or reused pid is left alone. Stdlib only (no psutil).
"""

import os
import signal
import subprocess
import time

STOP_GRACE_S = 10  # SIGTERM → SIGKILL grace period; tests monkeypatch it lower
_POLL_S = 0.05
_KILL_WAIT_S = 1  # after SIGKILL, how long to wait for the target to vanish


def _command_line(pid: int) -> str:
    """The process's full command line, or "" if ps fails or finds nothing."""
    try:
        result = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def engine_alive(pid, kind: str, run_id: str) -> bool:
    """True only if ``pid`` is a live process running ``<kind> _run <run_id>``
    (kind is "job" or "loop"). None, dead, zombie or reused pids → False.

    ps prints nothing for a dead pid and "<defunct>" for a zombie, so the
    command-line match alone proves the process exists, without signalling it.
    Padding with spaces matches whole words: id "job-ab" never matches "job-abc".
    """
    if pid is None or not run_id:
        return False
    return f" {kind} _run {run_id} " in f" {_command_line(pid).strip()} "


def stop_engine(pid, kind: str, run_id: str) -> bool:
    """Stop a verified engine and everything in its process group.

    SIGTERM the group (or the pid alone when the engine doesn't lead its own
    group, as with a foreground ``_run``), wait up to STOP_GRACE_S for it to
    go, then SIGKILL the same target. Returns True when it signalled, False
    (signalling nothing) when ``pid`` isn't this run's live engine.
    """
    if not engine_alive(pid, kind, run_id):
        return False
    try:
        group = os.getpgid(pid) == pid
    except (ProcessLookupError, PermissionError):
        group = False

    kill = os.killpg if group else os.kill

    def send(sig) -> None:
        try:
            kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def gone() -> bool:
        try:
            kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # Nothing left we may signal (macOS reports EPERM for a group of
            # only zombies): waiting longer can't change the outcome.
            return True
        return False

    def wait(seconds) -> bool:
        deadline = time.monotonic() + seconds
        while not gone():
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_S)
        return True

    send(signal.SIGTERM)
    if not wait(STOP_GRACE_S):
        send(signal.SIGKILL)
        wait(_KILL_WAIT_S)
    return True
