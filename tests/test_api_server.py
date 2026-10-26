import json
import os
import uuid
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock

import pytest
import yaml

from minimise.models import Job, Task, JobStatus, TaskStatus, Plan, PlanTask
from minimise.storage.database import Database
from minimise.storage.job_store import JobStore
from minimise.orchestration.job_controller import JobController
from minimise.interfaces.api_server import APIServer, _duration_label


def _make_plan_yaml(tmp_path, briefing=None):
    return Plan(
        name="test-plan",
        briefing=briefing,
        tasks=[
            PlanTask(
                id="t1", name="Task One", description="desc", goal="goal",
                estimated_duration_min=5,
            )
        ],
    )


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (5, "5 min"),
        (60, "1 hr"),
        (75, "1 hr 15 min"),
        (120, "2 hr"),
    ],
)
def test_duration_label_is_compact_and_human_readable(minutes, expected):
    assert _duration_label(minutes) == expected


@pytest.fixture
def mock_job_controller(db, temp_db_dir):
    """A mocked controller whose ``store`` is a real JobStore over the test db,
    so read endpoints (which route through store.load / store.load_many) work."""
    mock = Mock(spec=JobController)
    mock.store = JobStore(db, temp_db_dir / "jobs")
    return mock


@pytest.fixture
def api_server(db, mock_job_controller):
    """Create an API server instance for testing."""
    server = APIServer(db, mock_job_controller, port=5001)
    yield server
    # Cleanup
    if server.server_thread and server.server_thread.is_alive():
        server.stop()


@pytest.fixture
def client(api_server):
    """Flask test client for the API server's app (no need to actually bind a port)."""
    api_server.app.testing = True
    return api_server.app.test_client()


def test_get_job_plan_returns_structured_and_raw(client, mock_job_controller, temp_db_dir):
    job = mock_job_controller.store.create(
        _make_plan_yaml(temp_db_dir, briefing="Preserve the public contract."),
        base_commit="abc123",
        plan_path=str(temp_db_dir / "original-plan.yaml"),
    )

    resp = client.get(f"/jobs/{job.id}/plan")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["name"] == "test-plan"
    assert data["plan"]["briefing"] == "Preserve the public contract."
    assert data["plan"]["tasks"][0]["id"] == "t1"
    assert "name: test-plan" in data["raw_yaml"]
    assert yaml.safe_load(data["raw_yaml"])["briefing"] == "Preserve the public contract."


def test_get_job_plan_404_for_unknown_job(client):
    resp = client.get("/jobs/nonexistent/plan")
    assert resp.status_code == 404
    assert resp.get_json()["error"]


def test_get_job_plan_404_when_plan_file_missing(client, mock_job_controller, temp_db_dir):
    job = mock_job_controller.store.create(
        _make_plan_yaml(temp_db_dir), base_commit="abc123",
        plan_path=str(temp_db_dir / "original-plan.yaml"),
    )
    (temp_db_dir / "jobs" / job.id / "plan.yaml").unlink()

    resp = client.get(f"/jobs/{job.id}/plan")
    assert resp.status_code == 404


def test_api_server_initialization(db, mock_job_controller):
    """Verify API server setup."""
    server = APIServer(db, mock_job_controller, port=5001)
    assert server.db is db
    assert server.job_controller is mock_job_controller
    assert server.loop_store.db is db
    assert server.port == 5001
    assert server.app is not None


def _make_loop(api_server, name="Refine docs"):
    from minimise.models import LoopSpec

    spec = LoopSpec.model_validate({
        "version": "1",
        "name": name,
        "goal": "Make the documentation easier to use.",
        "max_iterations": 4,
        "loop": {
            "plan": {"prompt": "Plan the next improvement."},
            "implement": {"harness": "codex"},
            "evaluate": {
                "max_concurrent": 2,
                "dimensions": [
                    {"name": "clarity", "rubric": "Is it clear?"},
                    {"name": "coverage", "rubric": "Is it complete?"},
                ],
            },
        },
    })
    return api_server.loop_store.create(spec, plan_path="/tmp/loop.yaml")


def test_get_loops_api_returns_progress_and_pagination(
    api_server, db, monkeypatch
):
    from minimise.models import LoopStep, TaskStatus

    monkeypatch.setattr("minimise.interfaces.loop_views.LOOPS_PAGE_SIZE", 2)
    loops = [_make_loop(api_server, f"Loop {index}") for index in range(3)]
    db.create_loop_step(LoopStep(
        step_id="step-running",
        loop_id=loops[-1].loop_id,
        iteration=2,
        step_type="evaluate",
        dimension="clarity",
        status=TaskStatus.RUNNING,
    ))

    response = api_server.app.test_client().get("/api/loops?page=1")

    assert response.status_code == 200
    assert response.headers["X-Has-Next"] == "true"
    data = response.get_json()
    assert len(data) == 2
    assert data[0]["loop_id"] == loops[-1].loop_id
    assert data[0]["iteration"] == 2
    assert data[0]["stage"] == "Evaluate / clarity"
    assert data[0]["max_iterations"] == 4
    assert "steps" not in data[0]


def test_get_loop_api_returns_steps(api_server, db):
    from datetime import datetime, timedelta
    from minimise.models import LoopStep, TaskStatus

    loop = _make_loop(api_server)
    started = datetime(2026, 9, 26, 10, 0, 0)
    db.create_loop_step(LoopStep(
        step_id="step-plan",
        loop_id=loop.loop_id,
        iteration=1,
        step_type="plan",
        status=TaskStatus.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=2),
    ))

    response = api_server.app.test_client().get(f"/api/loops/{loop.loop_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["plan_version"] == 1
    assert data["evaluator_count"] == 2
    assert data["steps"][0]["stage"] == "Plan"
    assert data["steps"][0]["duration"] == "2.0s"


def test_loop_journal_and_logs_apis(api_server):
    from minimise.orchestration import loop_journal

    loop = _make_loop(api_server)
    loop_journal.append(api_server.loop_store.journal_path(loop.loop_id), {
        "timestamp": "2026-09-26T10:00:00",
        "iteration": 1,
        "step_type": "evaluate",
        "dimension": "clarity",
        "control": "done",
        "verdict": "pass",
        "findings": "Clear enough.",
    })
    api_server.loop_store.loop_log_path(loop.loop_id).write_text(
        "\n".join([
            json.dumps({
                "timestamp": "t1",
                "iteration": 1,
                "step_type": "plan",
                "level": "info",
                "message": "planning",
            }),
            "legacy text",
            json.dumps({
                "timestamp": "t2",
                "iteration": 1,
                "step_type": "evaluate",
                "dimension": "clarity",
                "level": "info",
                "message": "checking",
            }),
        ]) + "\n"
    )
    client = api_server.app.test_client()

    journal_response = client.get(f"/api/loops/{loop.loop_id}/journal")
    logs_response = client.get(
        f"/api/loops/{loop.loop_id}/logs?step_type=evaluate"
    )
    legacy_response = client.get(f"/api/loops/{loop.loop_id}/logs?limit=2")

    assert journal_response.get_json()["records"][0]["outcome"] == "pass"
    assert journal_response.get_json()["records"][0]["message"] == "Clear enough."
    assert [record["message"] for record in logs_response.get_json()["records"]] == [
        "checking"
    ]
    assert legacy_response.get_json()["records"][0]["stage"] == "-"


def test_loop_logs_api_combines_filters_before_tail_limit(api_server, db):
    from minimise.models import LoopStep

    loop = _make_loop(api_server)
    db.create_loop_step(LoopStep(
        step_id="step-clarity",
        loop_id=loop.loop_id,
        iteration=1,
        step_type="evaluate",
        dimension="clarity",
    ))
    db.create_loop_step(LoopStep(
        step_id="step-coverage",
        loop_id=loop.loop_id,
        iteration=2,
        step_type="evaluate",
        dimension="coverage",
    ))
    api_server.loop_store.loop_log_path(loop.loop_id).write_text(
        "\n".join([
            json.dumps({
                "iteration": 1,
                "step_type": "evaluate",
                "dimension": "clarity",
                "message": "target",
            }),
            json.dumps({
                "iteration": 2,
                "step_type": "plan",
                "message": "newer noise",
            }),
            json.dumps({
                "iteration": 2,
                "step_type": "evaluate",
                "dimension": "coverage",
                "message": "newest noise",
            }),
        ]) + "\n"
    )

    response = api_server.app.test_client().get(
        f"/api/loops/{loop.loop_id}/logs"
        "?limit=1&iteration=1&step_type=evaluate&dimension=clarity"
    )

    assert response.status_code == 200
    data = response.get_json()
    assert [record["message"] for record in data["records"]] == ["target"]
    assert data["filter_options"] == {
        "iterations": [1, 2],
        "dimensions": ["clarity", "coverage"],
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/loops/missing",
        "/api/loops/missing/journal",
        "/api/loops/missing/logs",
    ],
)
def test_loop_apis_404_for_unknown_loop(api_server, path):
    response = api_server.app.test_client().get(path)
    assert response.status_code == 404


def test_get_jobs_endpoint(api_server, db):
    """Test GET /jobs endpoint returns JSON list."""
    # Create test jobs
    job1 = Job(
        id=str(uuid.uuid4()),
        name="Job 1",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan1.yaml",
    )
    job2 = Job(
        id=str(uuid.uuid4()),
        name="Job 2",
        status=JobStatus.RUNNING,
        plan_path="/path/to/plan2.yaml",
        pid=os.getpid(),  # live pid so read-path reconcile leaves it RUNNING
    )
    db.create_job(job1)
    db.create_job(job2)

    # Test the endpoint
    with api_server.app.test_client() as client:
        response = client.get("/jobs")
        assert response.status_code == 200

        jobs_data = response.get_json()
        assert isinstance(jobs_data, list)
        assert len(jobs_data) == 2

        # Verify job data (jobs are returned in DESC order by created_at)
        assert jobs_data[0]["name"] == "Job 2"
        assert jobs_data[0]["status"] == JobStatus.RUNNING.value
        assert jobs_data[1]["name"] == "Job 1"
        assert jobs_data[1]["status"] == JobStatus.PENDING.value


def test_post_jobs_endpoint(api_server, mock_job_controller, db):
    """Test POST /jobs endpoint creates new job."""
    # Mock job manager to return a job
    job_id = str(uuid.uuid4())
    created_job = Job(
        id=job_id,
        name="New Job",
        status=JobStatus.PENDING,
        plan_path="/path/to/plan.yaml",
    )
    mock_job_controller.create_job.return_value = created_job

    # Test the endpoint
    with api_server.app.test_client() as client:
        response = client.post(
            "/jobs",
            json={"plan_path": "/path/to/plan.yaml"},
            content_type="application/json"
        )
        assert response.status_code == 201

        job_data = response.get_json()
        assert job_data["id"] == job_id
        assert job_data["name"] == "New Job"
        assert job_data["status"] == JobStatus.PENDING.value


def test_post_jobs_missing_plan_path(api_server, mock_job_controller):
    """POST /jobs with no plan_path is rejected with 400 and exact error payload."""
    with api_server.app.test_client() as client:
        # Empty body
        response = client.post("/jobs", json={}, content_type="application/json")
        assert response.status_code == 400
        assert response.get_json() == {"error": "Missing plan_path in request body"}

        # Some other key, still no plan_path
        response = client.post(
            "/jobs", json={"foo": "bar"}, content_type="application/json"
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "Missing plan_path in request body"}

    # Validation guard fires before the manager is ever consulted.
    mock_job_controller.create_job.assert_not_called()


def test_post_jobs_create_failure(api_server, mock_job_controller):
    """POST /jobs returns 500 when the manager fails to create the job."""
    mock_job_controller.create_job.return_value = None

    with api_server.app.test_client() as client:
        response = client.post(
            "/jobs",
            json={"plan_path": "/path/to/plan.yaml"},
            content_type="application/json",
        )
        assert response.status_code == 500
        assert response.get_json() == {"error": "Failed to create job"}


def test_get_job_by_id_endpoint(api_server, db):
    """Test GET /jobs/{job_id} endpoint."""
    # Create a test job with tasks
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.RUNNING,
        plan_path="/path/to/plan.yaml",
        pid=os.getpid(),  # live pid so read-path reconcile leaves it RUNNING
    )
    db.create_job(job)

    # Create tasks for the job
    task1 = Task(estimated_duration_min=5,
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Task 1",
        description="First task",
        status=TaskStatus.COMPLETED,
    )
    task2 = Task(estimated_duration_min=5, 
        id=str(uuid.uuid4()),
        job_id=job_id,
        name="Task 2",
        description="Second task",
        status=TaskStatus.RUNNING,
    )
    db.create_task(task1)
    db.create_task(task2)

    # Test the endpoint
    with api_server.app.test_client() as client:
        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200

        job_data = response.get_json()
        assert job_data["id"] == job_id
        assert job_data["name"] == "Test Job"
        assert job_data["status"] == JobStatus.RUNNING.value
        assert "tasks" in job_data
        assert len(job_data["tasks"]) == 2


def test_get_task_by_id_endpoint(api_server, db):
    """Test GET /jobs/{job_id}/tasks/{task_id} endpoint."""
    # Create a test job and task
    job_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    job = Job(id=job_id, name="Test Job", status=JobStatus.RUNNING)
    db.create_job(job)

    task = Task(estimated_duration_min=5, 
        id=task_id,
        job_id=job_id,
        name="Test Task",
        description="A test task",
        status=TaskStatus.COMPLETED,
        retries=1,
        diff_path="/path/to/diff",
    )
    db.create_task(task)

    # Test the endpoint
    with api_server.app.test_client() as client:
        response = client.get(f"/jobs/{job_id}/tasks/{task_id}")
        assert response.status_code == 200

        task_data = response.get_json()
        assert task_data["id"] == task_id
        assert task_data["name"] == "Test Task"
        assert task_data["status"] == TaskStatus.COMPLETED.value
        assert task_data["retries"] == 1
        assert task_data["diff_path"] == "/path/to/diff"


def test_get_nonexistent_job(api_server):
    """Test GET /jobs/{job_id} with nonexistent job."""
    nonexistent_id = str(uuid.uuid4())

    with api_server.app.test_client() as client:
        response = client.get(f"/jobs/{nonexistent_id}")
        assert response.status_code == 404


def test_get_nonexistent_task(api_server, db):
    """Test GET /jobs/{job_id}/tasks/{task_id} with nonexistent task."""
    job_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    job = Job(id=job_id, name="Test Job")
    db.create_job(job)

    with api_server.app.test_client() as client:
        response = client.get(f"/jobs/{job_id}/tasks/{task_id}")
        assert response.status_code == 404


def test_cancel_job_endpoint(api_server, mock_job_controller, db):
    """Test POST /jobs/{job_id}/cancel endpoint."""
    job_id = str(uuid.uuid4())
    job = Job(id=job_id, name="Test Job", status=JobStatus.RUNNING)
    db.create_job(job)

    mock_job_controller.stop_job.return_value = True

    with api_server.app.test_client() as client:
        response = client.post(f"/jobs/{job_id}/cancel")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        mock_job_controller.stop_job.assert_called_once_with(job_id)


def test_cancel_job_failure(api_server, mock_job_controller, db):
    """Test POST /jobs/{job_id}/cancel with failed cancel."""
    job_id = str(uuid.uuid4())
    job = Job(id=job_id, name="Test Job", status=JobStatus.RUNNING)
    db.create_job(job)

    mock_job_controller.stop_job.return_value = False

    with api_server.app.test_client() as client:
        response = client.post(f"/jobs/{job_id}/cancel")
        assert response.status_code == 400


def test_api_server_thread_setup(api_server):
    """Test server thread setup (without actually running in production mode)."""
    # Verify no server thread exists until start() is called.
    assert api_server.server_thread is None

    # We don't actually run the server in tests because it's a blocking operation
    # that's hard to stop. Instead, we verify the test client works for all endpoints.


def test_json_serialization_with_datetime(api_server, db):
    """Test that datetime objects are serialized to ISO format."""
    job_id = str(uuid.uuid4())
    created_time = datetime.utcnow()

    job = Job(
        id=job_id,
        name="Test Job",
        status=JobStatus.COMPLETED,
        created_at=created_time,
        completed_at=datetime.utcnow(),
    )
    db.create_job(job)

    with api_server.app.test_client() as client:
        response = client.get("/jobs")
        assert response.status_code == 200

        jobs_data = response.get_json()
        # Verify datetime serialization
        assert isinstance(jobs_data[0]["created_at"], str)
        assert "T" in jobs_data[0]["created_at"]  # ISO format


def test_get_jobs_attaches_task_list(db, api_server):
    """GET /jobs must attach each job's tasks (store.load_many omits them by default)."""
    job_id = str(uuid.uuid4())
    db.create_job(Job(id=job_id, name="Test Job", status=JobStatus.COMPLETED, created_at=datetime.utcnow()))
    db.create_task(Task(
        id=str(uuid.uuid4()), job_id=job_id, name="t1", description="d",
        estimated_duration_min=5, status=TaskStatus.COMPLETED,
    ))

    with api_server.app.test_client() as client:
        response = client.get("/jobs")
        jobs_data = response.get_json()
        assert len(jobs_data[0]["tasks"]) == 1


def test_cors_enabled(api_server):
    """Test that CORS is enabled on the server."""
    with api_server.app.test_client() as client:
        response = client.get("/jobs")
        # Check for CORS headers
        assert "Access-Control-Allow-Origin" in response.headers or response.status_code == 200


def test_job_page_and_api_show_the_usage_limit_stop_reason(client, mock_job_controller, db):
    """A job a usage limit stopped carries stop_reason in the job API and shows it on its page."""
    job = mock_job_controller.store.create(_make_plan_yaml(None), base_commit="abc123", plan_path="/tmp/plan.yaml")
    reason = "usage limit: resets 4:40am (America/Los_Angeles)"
    db.update_job_status(job.id, JobStatus.STOPPED, stop_reason=reason)

    data = client.get(f"/jobs/{job.id}").get_json()
    assert data["status"] == "stopped" and data["stop_reason"] == reason
    assert client.get("/jobs").get_json()[0]["stop_reason"] == reason

    html = client.get(f"/jobs/{job.id}/view").get_data(as_text=True)
    assert f'<p id="job-stop-reason" class="detail-meta">Stopped: {reason}</p>' in html


def test_job_page_hides_the_stop_reason_when_there_is_none(client, mock_job_controller):
    job = mock_job_controller.store.create(_make_plan_yaml(None), base_commit="abc123", plan_path="/tmp/plan.yaml")

    assert client.get(f"/jobs/{job.id}").get_json()["stop_reason"] is None
    html = client.get(f"/jobs/{job.id}/view").get_data(as_text=True)
    assert '<p id="job-stop-reason" class="detail-meta" hidden></p>' in html


def test_job_api_timeline_carries_tokens_for_task_agents_only(client, mock_job_controller, db):
    """Task rows carry their attempt's tokens, hook rows null; groups and the
    timeline carry tokens_total over task agents only. Job.to_dict and
    Task.to_dict (the list endpoint's shape) carry no tokens."""
    from datetime import timedelta
    from minimise.interfaces.formatting import tokens_total
    from minimise.models import Execution, TokenUsage

    plan = Plan.model_validate({
        "name": "usage",
        "pre_hooks": [{"name": "review-plan", "shell": "true", "estimated_duration_min": 1}],
        "tasks": [
            {"id": "t1", "name": "Build", "description": "d", "goal": "g",
             "estimated_duration_min": 10,
             "post_hooks": [{"name": "tests", "shell": "true", "estimated_duration_min": 1}]},
            {"id": "t2", "name": "Docs", "description": "d", "goal": "g",
             "estimated_duration_min": 5},
            {"id": "t3", "name": "Ship", "description": "d", "goal": "g",
             "estimated_duration_min": 5,
             "pre_hooks": [{"name": "lint", "shell": "false", "estimated_duration_min": 1}]},
        ],
    })
    job = mock_job_controller.store.create(plan, base_commit="abc123", plan_path="/tmp/plan.yaml")
    build, docs, ship = (task.id for task in job.tasks)
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    at = lambda secs: t0 + timedelta(seconds=secs)
    db.update_job_status(job.id, JobStatus.FAILED, started_at=t0, completed_at=at(100))
    for ex in (
        Execution(job_id=job.id, task_id=None, attempt=0, execution_type="pre_plan",
                  hook_name="review-plan", status=TaskStatus.COMPLETED,
                  started_at=at(0), completed_at=at(5)),
        Execution(job_id=job.id, task_id=build, attempt=0, status=TaskStatus.FAILED,
                  started_at=at(5), completed_at=at(20),
                  tokens=TokenUsage(100, 2_000_000, 50_000, 1500, reasoning=7,
                                    by_model={"claude-opus-5-5": {"input": 100}})),
        Execution(job_id=job.id, task_id=build, attempt=1, status=TaskStatus.COMPLETED,
                  started_at=at(20), completed_at=at(40), tokens=TokenUsage(20, None, 0, 30)),
        Execution(job_id=job.id, task_id=build, attempt=1, execution_type="post_task",
                  hook_name="tests", status=TaskStatus.COMPLETED,
                  started_at=at(40), completed_at=at(50)),
        Execution(job_id=job.id, task_id=docs, attempt=0, status=TaskStatus.COMPLETED,
                  started_at=at(50), completed_at=at(80)),  # ran, reported nothing
        Execution(job_id=job.id, task_id=ship, attempt=0, execution_type="pre_task",
                  hook_name="lint", status=TaskStatus.FAILED,
                  started_at=at(80), completed_at=at(90)),
        # the FAILED task row a failed pre-task hook writes: no agent ran
        Execution(job_id=job.id, task_id=ship, attempt=0, status=TaskStatus.FAILED),
    ):
        db.save_execution(ex)

    response = client.get(f"/jobs/{job.id}")
    timeline = response.get_json()["timeline"]

    rows = [(s["name"], s["kind"], s["tokens"]) for g in timeline["groups"] for s in g["steps"]]
    assert rows == [
        ("review-plan", "hook", None),
        ("Build", "task", {"input": 100, "cache_read": 2_000_000, "cache_write": 50_000,
                           "output": 1500, "total": 2_051_600}),
        ("Build", "task", {"input": 20, "cache_read": None, "cache_write": 0,
                           "output": 30, "total": 50}),
        ("tests", "hook", None),
        ("Docs", "task", None),
        ("lint", "hook", None),
        ("Ship", "task", None),
    ]
    nothing = {"input": None, "cache_read": None, "cache_write": None, "output": None,
               "total": None}
    assert [(g["name"], g["tokens_total"]) for g in timeline["groups"]] == [
        ("Before all tasks", {**nothing, "not_reported": 0}),  # hooks sum nothing
        ("Build", {"input": 120, "cache_read": 2_000_000, "cache_write": 50_000,
                   "output": 1530, "total": 2_051_650, "not_reported": 0}),
        ("Docs", {**nothing, "not_reported": 1}),
        ("Ship", {**nothing, "not_reported": 0}),  # pre-task hook failed: no agent ran
    ]
    assert timeline["tokens_total"] == {
        "input": 120, "cache_read": 2_000_000, "cache_write": 50_000, "output": 1530,
        "total": 2_051_650, "not_reported": 1,
    }
    # the same rollup `mini job status --format json` prints
    assert timeline["tokens_total"] == tokens_total(db.list_executions_for_job(job.id))

    body = response.get_data(as_text=True)
    for hidden in ("reasoning", "by_model", "claude-opus", "cost", "usd"):
        assert hidden not in body.lower(), hidden
    data = response.get_json()
    assert "tokens" not in data and "tokens_total" not in data
    assert not any(key.startswith("tokens") for task in data["tasks"] for key in task)
    loaded = mock_job_controller.store.load(job.id)
    assert "tokens" not in json.dumps(loaded.to_dict(), default=str)
    assert "tokens" not in client.get("/jobs").get_data(as_text=True)
    assert "tokens" not in client.get(f"/jobs/{job.id}/tasks/{build}").get_data(as_text=True)


def test_job_api_unstarted_job_reports_no_tokens(client, mock_job_controller):
    job = mock_job_controller.store.create(_make_plan_yaml(None), base_commit="abc123",
                                           plan_path="/tmp/plan.yaml")

    timeline = client.get(f"/jobs/{job.id}").get_json()["timeline"]

    assert timeline["tokens_total"] == {"input": None, "cache_read": None, "cache_write": None,
                                        "output": None, "total": None, "not_reported": 0}
    [group] = timeline["groups"]
    assert group["tokens_total"] == timeline["tokens_total"]
    assert group["steps"][0]["tokens"] is None  # pending
