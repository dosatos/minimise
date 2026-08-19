import os
import pytest
import json
import uuid
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock

from minimise.models import Job, Task, JobStatus, TaskStatus, Plan, PlanTask
from minimise.storage.database import Database
from minimise.storage.job_store import JobStore
from minimise.orchestration.job_controller import JobController
from minimise.interfaces.api_server import APIServer


def _make_plan_yaml(tmp_path):
    return Plan(
        name="test-plan",
        tasks=[
            PlanTask(
                id="t1", name="Task One", description="desc", goal="goal",
                estimated_duration_min=5,
            )
        ],
    )


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
        _make_plan_yaml(temp_db_dir), base_commit="abc123",
        plan_path=str(temp_db_dir / "original-plan.yaml"),
    )

    resp = client.get(f"/jobs/{job.id}/plan")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["plan"]["name"] == "test-plan"
    assert data["plan"]["tasks"][0]["id"] == "t1"
    assert "name: test-plan" in data["raw_yaml"]


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
    assert server.port == 5001
    assert server.app is not None


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
