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


def test_job_detail_page_keeps_execution_above_plan_and_logs_tabs(
    client, mock_job_controller
):
    job = _make_job(mock_job_controller)

    resp = client.get(f"/jobs/{job.id}/view")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'role="tablist"' in html
    assert 'data-detail-tab="details"' in html
    assert 'data-detail-tab="logs"' in html
    assert 'id="detail-panel-details"' in html
    assert 'id="detail-panel-logs"' in html
    execution_position = html.index('id="execution-heading"')
    tabs_position = html.index('role="tablist"')
    plan_position = html.index('id="plan-heading"')
    assert execution_position < tabs_position < plan_position
    logs_panel = html[html.index('id="detail-panel-logs"'):]
    assert "hidden" in logs_panel[:250]


def test_job_detail_page_renders_plan_as_human_readable_structure(
    client, mock_job_controller
):
    from minimise.models import Plan

    plan = Plan.model_validate({
        "name": "Readable migration",
        "briefing": "<script>alert('x')</script>\nCoordinate the rollout.",
        "pre_hooks": [{
            "name": "Validate plan",
            "estimated_duration_min": 5,
            "shell": "python scripts/validate.py",
        }],
        "tasks": [
            {
                "id": "schema",
                "name": "Design the schema",
                "goal": "Agree on a backwards-compatible contract.",
                "description": "Inspect current consumers.\nDocument migration risks.",
                "estimated_duration_min": 25,
                "timeout_min": 40,
                "harness": "codex",
                "model": "openai/gpt-5.5",
                "post_hooks": [{
                    "name": "Review schema",
                    "estimated_duration_min": 5,
                    "timeout_min": 10,
                    "shell": "python scripts/review.py",
                    "on_failure": "retry",
                }],
            },
            {
                "id": "migrate",
                "name": "Implement the migration",
                "goal": "Ship the new schema without downtime.",
                "description": "Add the migration and regression coverage.",
                "estimated_duration_min": 30,
                "assignee": "migration-owner",
            },
        ],
        "post_hooks": [{
            "name": "Verify rollout",
            "estimated_duration_min": 5,
            "shell": "pytest -q",
        }],
    })
    job = mock_job_controller.store.create(
        plan, base_commit="abc123", plan_path="/tmp/plan.yaml"
    )

    resp = client.get(f"/jobs/{job.id}/view")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert html.count('class="plan-task-card"') == 2
    assert html.count('<details class="plan-task-card" open>') == 2
    assert "Expand all" in html
    assert "Collapse all" in html
    assert "Execution blueprint" in html
    assert "<strong>1 hr 10 min</strong>" in html
    assert "Agree on a backwards-compatible contract." in html
    assert "Inspect current consumers.\nDocument migration risks." in html
    assert "codex · openai/gpt-5.5" in html
    assert "Persona · migration-owner" in html
    assert "Pre-plan" in html
    assert "Post-task" in html
    assert "Post-plan" in html
    assert "Briefing" in html
    assert "Coordinate the rollout." in html
    assert "Raw plan YAML" in html
    assert "<script>alert('x')</script>" not in html
    assert "&lt;script&gt;" in html


def test_job_detail_page_survives_missing_cached_plan(client, mock_job_controller):
    job = _make_job(mock_job_controller)
    plan_path = mock_job_controller.store.jobs_dir / job.id / "plan.yaml"
    plan_path.unlink()

    resp = client.get(f"/jobs/{job.id}/view")

    assert resp.status_code == 200
    assert b"Plan unavailable" in resp.data
    assert b"The cached plan file is missing for this job." in resp.data
    assert b"Execution" in resp.data
    assert b"Logs" in resp.data


def test_job_detail_page_preserves_raw_yaml_when_cached_plan_is_invalid(
    client, mock_job_controller
):
    job = _make_job(mock_job_controller)
    plan_path = mock_job_controller.store.jobs_dir / job.id / "plan.yaml"
    plan_path.write_text("name: [invalid")

    resp = client.get(f"/jobs/{job.id}/view")

    assert resp.status_code == 200
    assert b"Plan unavailable" in resp.data
    assert b"The cached plan could not be parsed" in resp.data
    assert b"Raw plan YAML" in resp.data
    assert b"name: [invalid" in resp.data


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
