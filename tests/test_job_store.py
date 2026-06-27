from minimise.storage.job_store import JobStore


def test_handoff_path_per_attempt_and_creates_parent(db, temp_db_dir):
    """handoff_path returns <jobs_dir>/<job>/handoffs/<task>/attempt-N.md and ensures its dir."""
    jobs_dir = temp_db_dir / "jobs"
    store = JobStore(db, jobs_dir)

    path = store.handoff_path("job-abc", "task-xyz", attempt=2)

    assert path == jobs_dir / "job-abc" / "handoffs" / "task-xyz" / "attempt-2.md"
    assert path.parent.exists()


def test_job_log_path_is_stable_and_creates_parent(db, temp_db_dir):
    """job_log_path returns <jobs_dir>/<job>/job.log (one file per job) and ensures its dir."""
    jobs_dir = temp_db_dir / "jobs"
    store = JobStore(db, jobs_dir)

    path = store.job_log_path("job-abc")

    assert path == jobs_dir / "job-abc" / "job.log"
    assert path.parent.exists()
    # Stable across calls.
    assert store.job_log_path("job-abc") == path
