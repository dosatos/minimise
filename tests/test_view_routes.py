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
