import pytest
from minimise.storage.job_store import JobStore
from minimise.orchestration.job_controller import JobController
from minimise.interfaces.api_server import APIServer
from unittest.mock import Mock


@pytest.fixture
def mock_job_controller(db, temp_db_dir):
    mock = Mock(spec=JobController)
    mock.store = JobStore(db, temp_db_dir / "jobs")
    return mock


@pytest.fixture
def client(db, mock_job_controller):
    server = APIServer(db, mock_job_controller, port=5002)
    server.app.testing = True
    return server.app.test_client()


def test_job_list_page_renders_empty_state(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"No jobs yet" in resp.data


def test_job_list_page_renders_job_row(client, mock_job_controller):
    from minimise.models import Plan, PlanTask

    plan = Plan(name="demo-plan", tasks=[
        PlanTask(id="t1", name="T1", description="d", goal="g", estimated_duration_min=5)
    ])
    job = mock_job_controller.store.create(plan, base_commit="abc123", plan_path="/tmp/plan.yaml")

    resp = client.get("/")
    assert resp.status_code == 200
    assert job.id.encode() in resp.data
    assert b"demo-plan" in resp.data


def test_job_detail_page_renders(client, mock_job_controller):
    from minimise.models import Plan, PlanTask

    plan = Plan(name="demo-plan", tasks=[
        PlanTask(id="t1", name="T1", description="d", goal="g", estimated_duration_min=5)
    ])
    job = mock_job_controller.store.create(plan, base_commit="abc123", plan_path="/tmp/plan.yaml")

    resp = client.get(f"/jobs/{job.id}/view")
    assert resp.status_code == 200
    assert job.id.encode() in resp.data
    assert b"demo-plan" in resp.data


def test_job_detail_page_404_for_unknown_job(client):
    resp = client.get("/jobs/nonexistent/view")
    assert resp.status_code == 404


def test_get_job_includes_hooks(client, mock_job_controller, db):
    from minimise.models import Execution, TaskStatus

    job = _make_job(mock_job_controller)

    resp = client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.get_json()["hooks"] == []

    db.save_execution(Execution(
        job_id=job.id, task_id=None, attempt=0, execution_type="pre_plan",
        hook_name="review-plan", status=TaskStatus.COMPLETED,
    ))

    resp = client.get(f"/jobs/{job.id}")
    assert resp.status_code == 200
    hooks = resp.get_json()["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["hook_name"] == "review-plan"
    assert hooks[0]["execution_type"] == "pre_plan"


def _make_job(mock_job_controller):
    from minimise.models import Plan, PlanTask

    plan = Plan(name="demo-plan", tasks=[
        PlanTask(id="t1", name="T1", description="d", goal="g", estimated_duration_min=5)
    ])
    return mock_job_controller.store.create(plan, base_commit="abc123", plan_path="/tmp/plan.yaml")


def test_job_logs_no_log_file_returns_empty_records(client, mock_job_controller):
    job = _make_job(mock_job_controller)
    resp = client.get(f"/jobs/{job.id}/logs")
    assert resp.status_code == 200
    assert resp.get_json() == {"records": []}


def test_job_logs_404_for_unknown_job(client):
    resp = client.get("/jobs/nonexistent/logs")
    assert resp.status_code == 404


def _write_log(mock_job_controller, job_id, lines):
    log_path = mock_job_controller.store.job_log_path(job_id)
    log_path.write_text("\n".join(lines) + "\n")


def test_job_logs_returns_all_tailed_records_unfiltered(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    lines = [
        json.dumps({"timestamp": "t1", "task_id": "t1", "level": "info", "message": "task attempt 1", "type": "task", "step": "T1"}),
        json.dumps({"timestamp": "t2", "task_id": "t1", "level": "error", "message": "task attempt 2 (retry)", "type": "task", "step": "T1  · try 2"}),
        json.dumps({"timestamp": "t3", "task_id": None, "level": "info", "message": "pre_plan hook", "type": "pre_plan", "step": "review-plan"}),
        json.dumps({"timestamp": "t4", "task_id": None, "level": "info", "message": "post_plan hook", "type": "post_plan", "step": "implementation-review"}),
    ]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs")
    assert resp.status_code == 200
    body = resp.get_json()
    records = body["records"]
    assert len(records) == 4
    assert [r["task_id"] for r in records] == ["t1", "t1", None, None]
    assert [r["hook_name"] for r in records] == [None, None, "review-plan", "implementation-review"]
    assert body["hook_names"] == ["implementation-review", "review-plan"]


def test_job_logs_filter_by_task_id(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    lines = [
        json.dumps({"timestamp": "t1", "task_id": "t1", "level": "info", "message": "a"}),
        json.dumps({"timestamp": "t2", "task_id": "t2", "level": "info", "message": "b"}),
        json.dumps({"timestamp": "t3", "task_id": None, "level": "info", "message": "hook"}),
    ]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs?task_id=t1")
    records = resp.get_json()["records"]
    assert len(records) == 1 and records[0]["message"] == "a"


def test_job_logs_filter_by_hook_name(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    lines = [
        json.dumps({"timestamp": "t1", "task_id": "t1", "level": "info", "message": "a", "type": "task", "step": "T1"}),
        json.dumps({"timestamp": "t2", "task_id": None, "level": "info", "message": "b", "type": "pre_plan", "step": "review-plan"}),
        json.dumps({"timestamp": "t3", "task_id": None, "level": "info", "message": "c", "type": "post_plan", "step": "implementation-review"}),
    ]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs?hook_name=review-plan")
    records = resp.get_json()["records"]
    assert len(records) == 1 and records[0]["message"] == "b"


def test_job_logs_hook_names_reflects_tailed_window(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    lines = [
        json.dumps({"timestamp": "t1", "task_id": None, "level": "info", "message": "old hook", "type": "pre_plan", "step": "old-hook"}),
        json.dumps({"timestamp": "t2", "task_id": None, "level": "info", "message": "recent hook", "type": "post_plan", "step": "recent-hook"}),
    ]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs?limit=1")
    body = resp.get_json()
    assert [r["message"] for r in body["records"]] == ["recent hook"]
    assert body["hook_names"] == ["recent-hook"]


def test_job_logs_filter_all_returns_everything(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    lines = [
        json.dumps({"timestamp": "t1", "task_id": "t1", "level": "info", "message": "a"}),
        json.dumps({"timestamp": "t2", "task_id": None, "level": "info", "message": "hook"}),
    ]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs?task_id=all")
    assert len(resp.get_json()["records"]) == 2


def test_job_logs_limit_tails_most_recent_lines(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    lines = [
        json.dumps({"timestamp": f"t{i}", "task_id": None, "level": "info", "message": f"line{i}"})
        for i in range(5)
    ]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs?limit=2")
    records = resp.get_json()["records"]
    assert [r["message"] for r in records] == ["line3", "line4"]


def test_job_logs_handles_blank_and_malformed_lines(client, mock_job_controller):
    import json

    job = _make_job(mock_job_controller)
    good = json.dumps({"timestamp": "t1", "task_id": None, "level": "info", "message": "ok"})
    lines = [good, "", "not valid json {{{"]
    _write_log(mock_job_controller, job.id, lines)

    resp = client.get(f"/jobs/{job.id}/logs")
    assert resp.status_code == 200
    records = resp.get_json()["records"]
    assert len(records) == 2
    malformed = [r for r in records if r["message"] == "not valid json {{{"]
    assert malformed and malformed[0]["task_id"] is None
