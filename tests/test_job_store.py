import os

from minimise.models import JobStatus, Plan, PlanTask
from minimise.storage.job_store import JobStore


def _new_job(store):
    plan = Plan(name="p", tasks=[PlanTask(id="t1", name="t1", description="d", goal="g", estimated_duration_min=1)])
    return store.create(plan, base_commit="abc", plan_path="plan.yaml")


def test_create_carries_harness_through_db_roundtrip(db, temp_db_dir):
    store = JobStore(db, temp_db_dir / "jobs")
    plan = Plan(name="p", tasks=[
        PlanTask(id="t1", name="t1", description="d", goal="g", estimated_duration_min=1, harness="codex"),
    ])
    job = store.create(plan, base_commit="abc", plan_path="plan.yaml")

    loaded = store.load(job.id)

    assert loaded.tasks[0].harness == "codex"


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


def test_mark_job_running_records_orchestrator_pid(db, temp_db_dir):
    """mark_job_running persists os.getpid() into jobs.pid (round-trip)."""
    store = JobStore(db, temp_db_dir / "jobs")
    plan = Plan(name="p", tasks=[PlanTask(id="t1", name="t1", description="d", goal="g", estimated_duration_min=1)])
    job = store.create(plan, base_commit="abc", plan_path="plan.yaml")

    store.mark_job_running(job.id)

    assert store.load(job.id).pid == os.getpid()


def _dead_pid():
    """A pid guaranteed not to be alive: spawn a child, reap it."""
    import subprocess
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_reconcile_downgrades_dead_running_to_failed(db, temp_db_dir):
    store = JobStore(db, temp_db_dir / "jobs")
    dead = _dead_pid()

    for pid in (dead, None):
        job = _new_job(store)
        db.update_job_status(job.id, JobStatus.RUNNING, pid=pid)
        loaded = store.load(job.id)
        assert loaded.status == JobStatus.FAILED  # in-memory object already corrected
        assert store.load(job.id).status == JobStatus.FAILED  # persisted + idempotent


def test_reconcile_leaves_live_running_and_other_statuses(db, temp_db_dir):
    store = JobStore(db, temp_db_dir / "jobs")

    live = _new_job(store)
    store.mark_job_running(live.id)  # records os.getpid() — alive
    assert store.load(live.id).status == JobStatus.RUNNING

    for status in (JobStatus.PENDING, JobStatus.COMPLETED, JobStatus.STOPPED, JobStatus.FAILED):
        job = _new_job(store)
        db.update_job_status(job.id, status, pid=_dead_pid())
        assert store.load(job.id).status == status


def test_create_carries_model_through_db_roundtrip(db, temp_db_dir):
    store = JobStore(db, temp_db_dir / "jobs")
    plan = Plan(name="p", tasks=[
        PlanTask(id="t1", name="t1", description="d", goal="g", estimated_duration_min=1, model="deepseek/deepseek-v4-flash"),
    ])
    job = store.create(plan, base_commit="abc", plan_path="plan.yaml")

    loaded = store.load(job.id)

    assert loaded.tasks[0].model == "deepseek/deepseek-v4-flash"


def test_load_many_downgrades_crashed_job_in_list(db, temp_db_dir):
    store = JobStore(db, temp_db_dir / "jobs")
    job = _new_job(store)
    db.update_job_status(job.id, JobStatus.RUNNING, pid=_dead_pid())

    listed = {j.id: j for j in store.load_many()}
    assert listed[job.id].status == JobStatus.FAILED
