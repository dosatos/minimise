import os
import signal
import sys
import threading
import time
import pytest
import subprocess
import tempfile
from pathlib import Path
from minimise.storage.database import Database

@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for test databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)

@pytest.fixture
def db(temp_db_dir):
    """Create a test database."""
    db_path = temp_db_dir / "test.db"
    db = Database(db_path)
    db.init_db()
    return db

@pytest.fixture
def seed_run():
    """seed_run(db, tokens, *, ended=True, **owner) -> run_id: one agent_runs row,
    started now and finished with `tokens` (None = not reported) unless ended=False
    (a call that never returned). owner: job_id, task_id, attempt (0-based) or
    loop_id, step_id."""
    from datetime import datetime

    def seed(db, tokens=None, *, ended=True, **owner):
        run_id = db.start_agent_run(**owner)
        if ended:
            db.finish_agent_run(run_id, datetime.utcnow(), tokens)
        return run_id
    return seed


@pytest.fixture
def untrack():
    """untrack(db, job_id=..., loop_id=...): mark a job or loop as made before
    agent_runs existed, as the v11 migration does."""
    import sqlite3

    def mark(db, *, job_id=None, loop_id=None):
        conn = sqlite3.connect(db.db_path)
        with conn:
            if job_id:
                conn.execute("UPDATE jobs SET tokens_untracked = 1 WHERE id = ?", (job_id,))
            if loop_id:
                conn.execute("UPDATE loops SET tokens_untracked = 1 WHERE loop_id = ?", (loop_id,))
        conn.close()
    return mark


@pytest.fixture
def mock_config_dir(temp_db_dir, monkeypatch):
    """Mock the minimise config directory for CLI tests."""
    config_dir = temp_db_dir / ".minimise"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Patch the CLI module's CONFIG_DIR, DB_PATH, JOBS_DIR
    monkeypatch.setattr("minimise.interfaces.cli.CONFIG_DIR", config_dir)
    monkeypatch.setattr("minimise.interfaces.cli.DB_PATH", config_dir / "minimise.db")
    monkeypatch.setattr("minimise.interfaces.cli.JOBS_DIR", config_dir / "jobs")

    yield config_dir


@pytest.fixture
def isolated_repo(monkeypatch, tmp_path):
    """Point cli.REPO_PATH at a clean, committed temp git repo.

    `mini job new` validates a clean git tree against cli.REPO_PATH (frozen to
    cwd at import). Without this, tests that invoke `job new` fail whenever the
    real working tree is dirty. This gives them a hermetic, always-clean repo.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (repo / "README.md").write_text("test repo\n")
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env)
    monkeypatch.setattr("minimise.interfaces.cli.REPO_PATH", repo)
    yield repo


# A stand-in engine: optionally ignores SIGTERM (inherited by its child), spawns
# a sleeping grandchild (which joins the engine's process group), writes the grandchild's pid
# atomically to argv[2], then sleeps. Its argv ends in `<kind> _run <run_id>`.
_FAKE_ENGINE = """
import os, signal, subprocess, sys, time
if sys.argv[1] == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
with open(sys.argv[2] + ".tmp", "w") as f:
    f.write(str(child.pid))
os.replace(sys.argv[2] + ".tmp", sys.argv[2])
time.sleep(600)
"""


_os_kill = os.kill  # teardown must work even while a test has os.kill patched


def _pid_gone(pid, timeout) -> bool:
    """Wait until no process has this pid (zombies included); True if it went."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            _os_kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


class FakeEngine:
    def __init__(self, proc, grandchild_pid):
        self.proc = proc
        self.pid = proc.pid
        self.grandchild_pid = grandchild_pid

    def engine_gone(self, timeout=5.0) -> bool:
        return _pid_gone(self.pid, timeout)

    def grandchild_gone(self, timeout=5.0) -> bool:
        return _pid_gone(self.grandchild_pid, timeout)


@pytest.fixture
def fake_engine(tmp_path):
    """Factory for a REAL engine process tree: ``fake_engine(kind, run_id)`` spawns
    an engine whose argv contains ``<kind> _run <run_id>``, leading its own session
    (own_session=False keeps it in pytest's group, like a foreground ``_run``),
    with a sleeping grandchild. A thread reaps the engine so it never lingers as a
    zombie. Teardown SIGKILLs whatever is left, by pid — never a process group."""
    script = tmp_path / "fake_engine.py"
    script.write_text(_FAKE_ENGINE)
    spawned = []

    def spawn(kind, run_id, *, ignore_term=False, own_session=True):
        pidfile = tmp_path / f"grandchild-{len(spawned)}.pid"
        proc = subprocess.Popen(
            [sys.executable, str(script), "ignore-term" if ignore_term else "-",
             str(pidfile), kind, "_run", run_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=own_session,
        )
        threading.Thread(target=proc.wait, daemon=True).start()
        deadline = time.monotonic() + 10
        while not pidfile.exists():
            assert time.monotonic() < deadline, "fake engine never started its grandchild"
            time.sleep(0.02)
        engine = FakeEngine(proc, int(pidfile.read_text()))
        spawned.append(engine)
        return engine

    yield spawn
    for engine in spawned:
        # Kill each pid only while it still runs our script, so a pid reused
        # after the test's stop is never touched.
        for pid, marker in ((engine.pid, str(script)), (engine.grandchild_pid, "time.sleep(600)")):
            out = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout
            if marker in out:
                try:
                    _os_kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
