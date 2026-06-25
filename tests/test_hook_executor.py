"""HookExecutor — backward compat (no store) and execution recording (with store)."""

from minimise.models import TaskStatus
from minimise.orchestration.hook_executor import HookExecutor


def test_run_without_store_unchanged():
    """Bare HookExecutor behaves byte-for-byte as before and records nothing."""
    h = HookExecutor()
    assert h.run("exit 0", "Pre-plan") is True
    assert h.run("", "Pre-plan") is True
    assert h.run("exit 1", "Post-plan") is False


def test_records_success(db):
    HookExecutor(db, job_id="job-1").run("exit 0", "Pre-plan")
    execs = db.list_executions_for_job("job-1")
    assert len(execs) == 1
    e = execs[0]
    assert e.execution_type == "pre_plan"
    assert e.task_id is None
    assert e.status == TaskStatus.COMPLETED
    assert e.started_at <= e.completed_at


def test_records_failure(db):
    HookExecutor(db, job_id="job-2").run("exit 1", "Post-plan")
    execs = db.list_executions_for_job("job-2")
    assert len(execs) == 1
    assert execs[0].execution_type == "post_plan"
    assert execs[0].status == TaskStatus.FAILED


def test_empty_command_records_nothing(db):
    assert HookExecutor(db, job_id="job-3").run("", "Pre-plan") is True
    assert db.list_executions_for_job("job-3") == []
