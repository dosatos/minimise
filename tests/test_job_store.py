from minimise.storage.job_store import JobStore


def test_handoff_path_per_attempt_and_creates_parent(db, temp_db_dir):
    """handoff_path returns <jobs_dir>/<job>/handoffs/<task>/attempt-N.md and ensures its dir."""
    jobs_dir = temp_db_dir / "jobs"
    store = JobStore(db, jobs_dir)

    path = store.handoff_path("job-abc", "task-xyz", attempt=2)

    assert path == jobs_dir / "job-abc" / "handoffs" / "task-xyz" / "attempt-2.md"
    assert path.parent.exists()
