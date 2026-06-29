import json
from pathlib import Path
from minimise.orchestration.hook_executor import HookExecutor
from minimise.models import Hook
from minimise.logging.backend import JsonlLogBackend


def test_run_success_returns_true():
    h = Hook(name="ok", command="exit 0", estimated_duration_min=1)
    assert HookExecutor().run(h, "post_task", task_id="t1") is True


def test_run_failure_returns_false():
    h = Hook(name="bad", command="exit 1", estimated_duration_min=1)
    assert HookExecutor().run(h, "post_task", task_id="t1") is False


def test_records_execution_with_hook_name(tmp_path):
    from minimise.storage.database import Database
    from minimise.models import Job, JobStatus
    from datetime import datetime
    db = Database(tmp_path / "t.db"); db.init_db()
    db.create_job(Job(id="j1", name="J", status=JobStatus.RUNNING, created_at=datetime.utcnow()))
    HookExecutor(store=db, job_id="j1").run(
        Hook(name="pytest", command="exit 1", estimated_duration_min=1),
        "post_task", task_id="t1")
    rows = db.list_executions_for_job("j1")
    assert any(r.hook_name == "pytest" and r.execution_type == "post_task" for r in rows)


def test_failed_hook_logs_error_line(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="pytest", command="exit 1", estimated_duration_min=1),
        "post_task", task_id="t1")
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    err = [r for r in recs if r["level"] == "error"]
    assert err and err[0]["type"] == "post_task"
    assert "post_task_hook#pytest" in err[0]["execution_id"]
