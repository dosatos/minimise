"""engine_process: verify an engine pid by its command line, and stop its whole
process tree (SIGTERM the group, SIGKILL after the grace period).

These run REAL process trees from the ``fake_engine`` fixture (conftest.py): an
engine in its own session with a sleeping grandchild. Nothing here ever signals
pytest's own process group.
"""

import os
import signal
import subprocess
import sys
import time

import pytest

from minimise.orchestration import engine_process
from minimise.orchestration.engine_process import engine_alive, stop_engine


def test_stop_engine_kills_engine_and_grandchild(fake_engine):
    engine = fake_engine("job", "job-abc123")
    assert engine_alive(engine.pid, "job", "job-abc123")

    started = time.monotonic()
    assert stop_engine(engine.pid, "job", "job-abc123") is True
    assert time.monotonic() - started < engine_process.STOP_GRACE_S  # SIGTERM sufficed

    assert engine.engine_gone()
    assert engine.grandchild_gone()
    assert engine.proc.returncode == -signal.SIGTERM
    assert not engine_alive(engine.pid, "job", "job-abc123")


def test_stop_engine_escalates_to_sigkill(fake_engine, monkeypatch):
    """An engine (and grandchild) ignoring SIGTERM dies to SIGKILL after the grace."""
    monkeypatch.setattr(engine_process, "STOP_GRACE_S", 0.5)
    engine = fake_engine("job", "job-def456", ignore_term=True)

    started = time.monotonic()
    assert stop_engine(engine.pid, "job", "job-def456") is True
    assert time.monotonic() - started >= 0.5  # waited out the grace first

    assert engine.engine_gone()
    assert engine.grandchild_gone()
    assert engine.proc.returncode == -signal.SIGKILL


def test_stop_engine_signals_pid_alone_when_not_a_group_leader(fake_engine, monkeypatch):
    """A foreground `_run` shares its caller's group: only the pid is signalled,
    never the group (here: pytest's own)."""
    engine = fake_engine("job", "job-0f0f0f", own_session=False)
    assert os.getpgid(engine.pid) != engine.pid
    killpg_calls = []
    monkeypatch.setattr(engine_process.os, "killpg", lambda *a: killpg_calls.append(a))

    assert stop_engine(engine.pid, "job", "job-0f0f0f") is True

    assert killpg_calls == []
    assert engine.engine_gone()
    assert engine.proc.returncode == -signal.SIGTERM
    assert not engine.grandchild_gone(timeout=0.3)  # outside the signalled target


def test_engine_alive_matches_kind_and_whole_id(fake_engine):
    engine = fake_engine("loop", "loop-a1b2c3")
    assert engine_alive(engine.pid, "loop", "loop-a1b2c3")
    assert not engine_alive(engine.pid, "job", "loop-a1b2c3")  # wrong kind
    assert not engine_alive(engine.pid, "loop", "loop-ffffff")  # another run
    assert not engine_alive(engine.pid, "loop", "loop-a1b2")    # a prefix of the id


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # reaped: the pid no longer exists
    return proc.pid


@pytest.mark.parametrize("which", ["none", "dead", "live-not-an-engine"])
def test_non_engine_pids_are_never_signalled(which, monkeypatch):
    pid = {"none": None, "dead": _dead_pid(), "live-not-an-engine": os.getpid()}[which]
    assert engine_alive(pid, "job", "job-abc123") is False

    calls = []
    monkeypatch.setattr(engine_process.os, "kill", lambda *a: calls.append(("kill", a)))
    monkeypatch.setattr(engine_process.os, "killpg", lambda *a: calls.append(("killpg", a)))
    assert stop_engine(pid, "job", "job-abc123") is False
    assert calls == []


def test_engine_alive_is_false_when_ps_fails(fake_engine, monkeypatch):
    engine = fake_engine("job", "job-abc123")

    def broken_run(*args, **kwargs):
        raise OSError("ps not found")

    monkeypatch.setattr(engine_process.subprocess, "run", broken_run)
    assert engine_alive(engine.pid, "job", "job-abc123") is False
