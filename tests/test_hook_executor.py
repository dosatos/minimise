import json
from pathlib import Path
from minimise.orchestration.hook_executor import HookExecutor
from minimise.models import Hook
from minimise.logging.backend import JsonlLogBackend


def test_run_success_returns_true():
    h = Hook(name="ok", shell="exit 0", estimated_duration_min=1)
    assert HookExecutor().run(h, "post_task", task_id="t1")[0] is True


def test_run_failure_returns_false():
    h = Hook(name="bad", shell="exit 1", estimated_duration_min=1)
    assert HookExecutor().run(h, "post_task", task_id="t1")[0] is False


def test_records_execution_with_hook_name(tmp_path):
    from minimise.storage.database import Database
    from minimise.models import Job, JobStatus
    from datetime import datetime
    db = Database(tmp_path / "t.db"); db.init_db()
    db.create_job(Job(id="j1", name="J", status=JobStatus.RUNNING, created_at=datetime.utcnow()))
    HookExecutor(store=db, job_id="j1").run(
        Hook(name="pytest", shell="exit 1", estimated_duration_min=1),
        "post_task", task_id="t1")
    rows = db.list_executions_for_job("j1")
    assert any(r.hook_name == "pytest" and r.execution_type == "post_task" for r in rows)


def test_failed_hook_logs_error_line(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="pytest", shell="exit 1", estimated_duration_min=1),
        "post_task", task_id="t1")
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    err = [r for r in recs if r["level"] == "error"]
    assert err and err[0]["type"] == "post_task"
    assert "post_task_hook#pytest" in err[0]["execution_id"]


def test_success_hook_logs_info_line(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="lint", shell="exit 0", estimated_duration_min=1),
        "pre_plan", task_id=None)
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    info = [r for r in recs if r["level"] == "info"]
    assert info and "pre_plan_hook#lint" in info[0]["execution_id"]


def test_runs_in_repo_root_cwd(tmp_path):
    h = Hook(name="pwd", shell="pwd", estimated_duration_min=1)
    ex = _captured_execution(HookExecutor(repo_root=tmp_path), h)
    assert str(tmp_path.resolve()) in ex.output


def test_runs_in_project_venv(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    h = Hook(name="env", shell="echo $VIRTUAL_ENV", estimated_duration_min=1)
    ex = _captured_execution(HookExecutor(repo_root=tmp_path), h)
    assert str((tmp_path / ".venv")) in ex.output


def test_run_pipes_stdin_to_hook(tmp_path):
    h = Hook(name="readplan", shell="cat", estimated_duration_min=1)
    ex = _captured_execution_with_stdin(HookExecutor(repo_root=tmp_path), h, "PLAN-YAML-HERE")
    assert "PLAN-YAML-HERE" in ex.output


def _captured_execution_with_stdin(executor, hook, stdin):
    captured = []
    executor.store = type("S", (), {"save_execution": lambda self, e: captured.append(e)})()
    executor.run(hook, "pre_plan", task_id=None, stdin=stdin)
    return captured[0]


def _captured_execution(executor, hook):
    """Run a hook, capturing the Execution it would persist."""
    captured = []
    executor.store = type("S", (), {"save_execution": lambda self, e: captured.append(e)})()
    executor.run(hook, "post_task", task_id="t1")
    return captured[0]


def test_hook_log_records_carry_step(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="lint", shell="exit 0", estimated_duration_min=1),
        "pre_plan", task_id=None)
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    assert recs and all(r["step"] == "lint" for r in recs)


def test_failing_hook_multiline_output_makes_one_record_per_line(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="pytest", shell="printf 'line1\\nline2\\n'; exit 1",
             estimated_duration_min=1),
        "post_task", task_id="t1")
    errs = [json.loads(l) for l in log.read_text().splitlines() if json.loads(l)["level"] == "error"]
    msgs = [r["message"] for r in errs]
    assert "line1" in msgs and "line2" in msgs
    assert all(r["step"] == "pytest" for r in errs)


def test_empty_output_hook_still_emits_status_line(tmp_path):
    log = tmp_path / "job.log"
    HookExecutor(job_id="j1", log_path=log, backend=JsonlLogBackend()).run(
        Hook(name="noop", shell="exit 0", estimated_duration_min=1),
        "pre_plan", task_id=None)
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    assert [r for r in recs if r["level"] == "info" and r["step"] == "noop"]
