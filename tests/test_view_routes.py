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
def api_server(db, mock_job_controller):
    server = APIServer(db, mock_job_controller, port=5002)
    server.app.testing = True
    return server


@pytest.fixture
def client(api_server):
    return api_server.app.test_client()


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


def _make_loop(api_server):
    from minimise.models import LoopSpec

    spec = LoopSpec.model_validate({
        "version": "1",
        "name": "Refine the guide",
        "goal": "<script>alert('goal')</script>\nMake onboarding effortless.",
        "max_iterations": 3,
        "loop": {
            "plan": {"prompt": "Choose the highest-impact gap."},
            "implement": {"prompt_file": "prompts/implement.md", "harness": "codex"},
            "evaluate": {
                "max_concurrent": 2,
                "dimensions": [{
                    "name": "clarity",
                    "rubric": "Can a new user follow it?",
                    "persona": "mini:doc-review:clarity",
                }],
            },
        },
    })
    return api_server.loop_store.create(spec, plan_path="/tmp/loop.yaml")


def test_loop_list_page_renders_empty_state_and_nav(client):
    response = client.get("/loops/")

    assert response.status_code == 200
    assert b"No loops yet" in response.data
    assert b"Loops" in response.data


def test_loop_list_page_renders_loop_progress(client, api_server, db):
    from minimise.models import LoopStep, TaskStatus

    loop = _make_loop(api_server)
    db.create_loop_step(LoopStep(
        step_id="step-eval",
        loop_id=loop.loop_id,
        iteration=2,
        step_type="evaluate",
        dimension="clarity",
        status=TaskStatus.RUNNING,
    ))

    response = client.get("/loops")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert loop.loop_id in html
    assert "Refine the guide" in html
    assert "2/3" in html
    assert "Evaluate / clarity" in html
    assert f"/loops/{loop.loop_id}/view" in html


def test_loop_detail_page_renders_spec_steps_journal_and_logs(
    client, api_server, db
):
    import json
    from datetime import datetime, timedelta
    from minimise.models import JobStatus, LoopStep, TaskStatus
    from minimise.orchestration import loop_journal

    loop = _make_loop(api_server)
    started = datetime(2026, 9, 26, 10, 0, 0)
    db.update_loop_status(
        loop.loop_id,
        status=JobStatus.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=8),
    )
    db.create_loop_step(LoopStep(
        step_id="step-plan",
        loop_id=loop.loop_id,
        iteration=1,
        step_type="plan",
        status=TaskStatus.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=2),
    ))
    db.create_loop_step(LoopStep(
        step_id="step-eval",
        loop_id=loop.loop_id,
        iteration=1,
        step_type="evaluate",
        dimension="clarity",
        status=TaskStatus.COMPLETED,
        started_at=started + timedelta(seconds=3),
        completed_at=started + timedelta(seconds=5),
    ))
    loop_journal.append(api_server.loop_store.journal_path(loop.loop_id), {
        "timestamp": "2026-09-26T10:00:05",
        "iteration": 1,
        "step_type": "evaluate",
        "dimension": "clarity",
        "control": "done",
        "verdict": "pass",
        "findings": "The guide is clear.",
    })
    api_server.loop_store.loop_log_path(loop.loop_id).write_text(json.dumps({
        "timestamp": "2026-09-26T10:00:04",
        "iteration": 1,
        "step_type": "evaluate",
        "dimension": "clarity",
        "level": "info",
        "message": "Reviewed the onboarding flow.",
    }) + "\n")

    response = client.get(f"/loops/{loop.loop_id}/view")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.index('id="loop-execution-heading"') < html.index('role="tablist"')
    assert 'data-detail-tab="details"' in html
    assert 'data-detail-tab="journal"' in html
    assert 'data-detail-tab="logs"' in html
    assert 'id="loop-log-iteration-filter"' in html
    assert '<option value="1">Iteration 1</option>' in html
    assert 'id="loop-log-stage-filter"' in html
    assert 'id="loop-log-dimension-filter"' in html
    assert '<option value="clarity">clarity</option>' in html
    assert "1/3" in html
    assert "Evaluate" in html
    assert "Choose the highest-impact gap." in html
    assert "prompts/implement.md" in html
    assert "Harness: codex" in html
    assert "Can a new user follow it?" in html
    assert "Persona: mini:doc-review:clarity" in html
    assert "The guide is clear." in html
    assert "Reviewed the onboarding flow." in html
    assert "Raw loop YAML" in html
    assert "<script>alert('goal')</script>" not in html
    assert "&lt;script&gt;" in html


def test_loop_detail_page_survives_missing_cached_spec(
    client, api_server
):
    loop = _make_loop(api_server)
    (api_server.loop_store.jobs_dir / loop.loop_id / "plan.yaml").unlink()

    response = client.get(f"/loops/{loop.loop_id}/view")

    assert response.status_code == 200
    assert b"Loop spec unavailable" in response.data
    assert b"The cached loop spec is missing." in response.data
    assert b"Iteration execution" in response.data
    assert b"Journal" in response.data


def test_loop_detail_page_preserves_invalid_raw_spec(client, api_server):
    loop = _make_loop(api_server)
    path = api_server.loop_store.jobs_dir / loop.loop_id / "plan.yaml"
    path.write_text("name: [invalid")

    response = client.get(f"/loops/{loop.loop_id}/view")

    assert response.status_code == 200
    assert b"Loop spec unavailable" in response.data
    assert b"The cached loop spec could not be parsed" in response.data
    assert b"Raw loop YAML" in response.data
    assert b"name: [invalid" in response.data


def test_loop_detail_page_404_for_unknown_loop(client):
    response = client.get("/loops/missing/view")
    assert response.status_code == 404


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
    assert plan.briefing == "<script>alert('x')</script>\nCoordinate the rollout."
    assert "briefing" not in (plan.model_extra or {})
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


def _timeline_plan():
    from minimise.models import Plan

    return Plan.model_validate({
        "name": "timed",
        "pre_hooks": [{"name": "review-plan", "shell": "true", "estimated_duration_min": 5}],
        "tasks": [
            {"id": "t1", "name": "Build", "description": "d", "goal": "g",
             "estimated_duration_min": 10,
             "post_hooks": [{"name": "tests", "shell": "true", "estimated_duration_min": 2}]},
            {"id": "t2", "name": "Docs", "description": "d", "goal": "g",
             "estimated_duration_min": 20},
        ],
    })


def test_job_timeline_times_each_step_from_job_start():
    from datetime import datetime, timedelta
    from minimise.interfaces.api_server import _job_timeline
    from minimise.interfaces.timeline import build_steps
    from minimise.models import Execution, Job, JobStatus, Task, TaskStatus

    t0 = datetime(2026, 1, 1, 12, 0, 0)
    at = lambda secs: t0 + timedelta(seconds=secs)
    job = Job(id="j1", name="timed", status=JobStatus.RUNNING, started_at=t0)
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d", estimated_duration_min=10),
             Task(id="task-2", job_id="j1", name="Docs", description="d", estimated_duration_min=20)]
    execs = [
        Execution(job_id="j1", task_id=None, attempt=0, execution_type="pre_plan",
                  hook_name="review-plan", status=TaskStatus.COMPLETED,
                  started_at=at(0), completed_at=at(240)),
        Execution(job_id="j1", task_id="task-1", attempt=0, status=TaskStatus.COMPLETED,
                  started_at=at(240), completed_at=at(900)),
        Execution(job_id="j1", task_id="task-1", attempt=0, execution_type="post_task",
                  hook_name="tests", status=TaskStatus.RUNNING, started_at=at(900)),
    ]

    timeline = _job_timeline(job, build_steps(_timeline_plan(), tasks, execs), at(960))

    groups = timeline["groups"]
    assert [(g["kind"], g["name"], g["task_id"]) for g in groups] == [
        ("plan_hooks", "Before all tasks", None),
        ("task", "Build", "task-1"),
        ("task", "Docs", "task-2"),
    ]
    steps = [s for g in groups for s in g["steps"]]
    assert [(s["name"], s["kind"], s["phase"]) for s in steps] == [
        ("review-plan", "hook", "pre_plan"),
        ("Build", "task", "task"),
        ("tests", "hook", "post_task"),
        ("Docs", "task", "task"),
    ]
    assert [s["start_offset"] for s in steps] == [0.0, 240.0, 900.0, None]
    assert [s["duration"] for s in steps] == [240.0, 660.0, None, None]
    assert steps[1]["attempt"] == 1 and steps[1]["estimate_secs"] == 600
    assert timeline["now_offset"] == 960.0
    # running hook projects to its 2 min estimate, then Docs chains after it
    assert steps[2]["bar"] == {"start": 900.0, "actual_end": 960.0, "projected_end": 1020.0}
    assert steps[3]["bar"] == {"start": 1020.0, "actual_end": 1020.0, "projected_end": 2220.0}
    assert timeline["total_secs"] == 2220.0
    assert timeline["planned_secs"] == (5 + 10 + 2 + 20) * 60


def test_job_timeline_finished_job_does_not_project_unrun_steps():
    from datetime import datetime, timedelta
    from minimise.interfaces.api_server import _job_timeline
    from minimise.interfaces.timeline import build_steps
    from minimise.models import Execution, Job, JobStatus, Task, TaskStatus

    t0 = datetime(2026, 1, 1, 12, 0, 0)
    job = Job(id="j1", name="timed", status=JobStatus.FAILED,
              started_at=t0, completed_at=t0 + timedelta(seconds=300))
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d", estimated_duration_min=10)]
    execs = [Execution(job_id="j1", task_id=None, attempt=0, execution_type="pre_plan",
                       hook_name="review-plan", status=TaskStatus.FAILED,
                       started_at=t0, completed_at=t0 + timedelta(seconds=300))]

    timeline = _job_timeline(job, build_steps(_timeline_plan(), tasks, execs),
                             t0 + timedelta(hours=5))

    assert timeline["now_offset"] == 300.0  # frozen at completion, not "now"
    assert timeline["total_secs"] == 300.0
    steps = [s for g in timeline["groups"] for s in g["steps"]]
    assert [s["bar"] is None for s in steps] == [False, True, True, True]
    assert [g["status"] for g in timeline["groups"]] == ["failed", "pending", "pending"]


def test_job_timeline_unstarted_job_lays_out_estimates_from_zero():
    from datetime import datetime
    from minimise.interfaces.api_server import _job_timeline
    from minimise.interfaces.timeline import build_steps
    from minimise.models import Job

    timeline = _job_timeline(Job(id="j1", name="timed"),
                             build_steps(_timeline_plan(), [], []), datetime(2026, 1, 1))

    assert timeline["now_offset"] is None and timeline["run_start_offset"] is None
    groups = timeline["groups"]
    # no task rows yet, so steps carry no task_id: plan order still splits the tasks
    assert [(g["name"], [s["name"] for s in g["steps"]]) for g in groups] == [
        ("Before all tasks", ["review-plan"]), ("Build", ["Build", "tests"]), ("Docs", ["Docs"]),
    ]
    assert [s["bar"]["start"] for g in groups for s in g["steps"]] == [0.0, 300.0, 900.0, 1020.0]
    assert [g["duration"] for g in groups] == [None, None, None]
    assert timeline["total_secs"] == 2220.0


def test_get_job_includes_timeline_in_plan_order(client, mock_job_controller):
    job = mock_job_controller.store.create(
        _timeline_plan(), base_commit="abc123", plan_path="/tmp/plan.yaml"
    )

    resp = client.get(f"/jobs/{job.id}")

    assert resp.status_code == 200
    data = resp.get_json()
    groups = data["timeline"]["groups"]
    assert [g["name"] for g in groups] == ["Before all tasks", "Build", "Docs"]
    assert [g["task_id"] for g in groups] == [None, job.tasks[0].id, job.tasks[1].id]
    steps = [s for g in groups for s in g["steps"]]
    assert [s["name"] for s in steps] == ["review-plan", "Build", "tests", "Docs"]
    assert [s["status"] for s in steps] == ["pending"] * 4
    assert steps[1]["task_id"] == job.tasks[0].id
    assert data["hooks"] == []


def test_get_job_timeline_falls_back_to_executions_without_a_plan(
    client, mock_job_controller, db
):
    from minimise.models import Execution, TaskStatus

    job = _make_job(mock_job_controller)
    (mock_job_controller.store.jobs_dir / job.id / "plan.yaml").write_text("name: [invalid")
    db.save_execution(Execution(
        job_id=job.id, task_id=None, attempt=0, execution_type="pre_plan",
        hook_name="review-plan", status=TaskStatus.COMPLETED,
    ))

    resp = client.get(f"/jobs/{job.id}")

    assert resp.status_code == 200
    groups = resp.get_json()["timeline"]["groups"]
    assert [(g["kind"], g["name"]) for g in groups] == [
        ("plan_hooks", "Before all tasks"), ("task", "T1"),
    ]
    assert [[(s["name"], s["kind"]) for s in g["steps"]] for g in groups] == [
        [("review-plan", "hook")], [("T1", "task")],
    ]


def test_job_detail_page_renders_timing_summary_and_timeline(client, mock_job_controller):
    job = _make_job(mock_job_controller)

    html = client.get(f"/jobs/{job.id}/view").get_data(as_text=True)

    for element_id in ("job-elapsed", "job-progress", "job-current", "job-remaining", "timeline-rows"):
        assert f'id="{element_id}"' in html
    assert ">Duration<" in html and ">Estimate<" in html and ">Timeout<" in html
    assert 'class="col-timeout"' in html
    assert ">Started<" not in html
    assert "data-local-time" in html


def _at(secs):
    """A naive-UTC moment ``secs`` seconds after the test job's start."""
    from datetime import datetime, timedelta
    return datetime(2026, 1, 1, 12, 0, 0) + timedelta(seconds=secs)


def _grouped_timeline(plan_dict, tasks, execs, job_kwargs, now_secs):
    """Build a job timeline from a plan dict at ``now_secs``."""
    from minimise.interfaces.api_server import _job_timeline
    from minimise.interfaces.timeline import build_steps
    from minimise.models import Job, Plan

    job = Job(id="j1", name="timed", tasks=tasks, **job_kwargs)
    return _job_timeline(job, build_steps(Plan.model_validate(plan_dict), tasks, execs),
                         _at(now_secs))


def _task(task_id, name, estimate, status=None, **kwargs):
    from minimise.models import Task, TaskStatus
    return Task(id=task_id, job_id="j1", name=name, description="d",
                estimated_duration_min=estimate, status=status or TaskStatus.PENDING, **kwargs)


def _hooked_plan(**task_extra):
    return {
        "name": "timed",
        "tasks": [{
            "id": "t1", "name": "Pictures by id", "description": "d", "goal": "g",
            "estimated_duration_min": 75, "timeout_min": 150,
            "post_hooks": [
                {"name": "tests", "shell": "true", "estimated_duration_min": 3, "timeout_min": 10},
                {"name": "review-implementation", "shell": "true", "estimated_duration_min": 8},
            ],
            **task_extra,
        }],
    }


def test_job_timeline_task_group_spans_first_start_to_last_end():
    """a) attempt + two post_task hooks back to back (job-214b37, task-dfc8ce)."""
    from minimise.models import Execution, JobStatus, TaskStatus

    tasks = [_task("task-dfc8ce", "Pictures by id", 75, TaskStatus.COMPLETED)]
    execs = [
        Execution(job_id="j1", task_id="task-dfc8ce", attempt=0, status=TaskStatus.COMPLETED,
                  started_at=_at(0), completed_at=_at(926.8)),
        Execution(job_id="j1", task_id="task-dfc8ce", attempt=0, execution_type="post_task",
                  hook_name="tests", status=TaskStatus.COMPLETED,
                  started_at=_at(926.8), completed_at=_at(926.8 + 38.3)),
        Execution(job_id="j1", task_id="task-dfc8ce", attempt=0, execution_type="post_task",
                  hook_name="review-implementation", status=TaskStatus.COMPLETED,
                  started_at=_at(926.8 + 38.3), completed_at=_at(926.8 + 38.3 + 152.9)),
    ]

    timeline = _grouped_timeline(_hooked_plan(), tasks, execs,
                                 {"status": JobStatus.COMPLETED, "started_at": _at(0),
                                  "completed_at": _at(1118.0)}, 5000)

    [group] = timeline["groups"]
    assert (group["kind"], group["name"], group["task_id"], group["status"]) == (
        "task", "Pictures by id", "task-dfc8ce", "completed")
    assert [s["duration"] for s in group["steps"]] == [926.8, 38.3, 152.9]
    assert group["start_offset"] == 0.0
    assert group["duration"] == 1118.0  # wall time, first start to last end
    assert group["estimate_secs"] == (75 + 3 + 8) * 60
    assert "bar" not in group and "timeout_secs" not in group
    assert timeline["planned_secs"] == (75 + 3 + 8) * 60


def test_job_timeline_retried_task_counts_its_estimate_once():
    """b) two attempts of one task: the task estimate is summed once."""
    from minimise.models import Execution, JobStatus, TaskStatus

    tasks = [_task("task-1", "Pictures by id", 75, TaskStatus.COMPLETED, retries=1)]
    execs = [
        Execution(job_id="j1", task_id="task-1", attempt=0, status=TaskStatus.FAILED,
                  started_at=_at(0), completed_at=_at(100), exit_reason="hook_retry"),
        Execution(job_id="j1", task_id="task-1", attempt=1, status=TaskStatus.COMPLETED,
                  started_at=_at(150), completed_at=_at(300)),
        Execution(job_id="j1", task_id="task-1", attempt=0, execution_type="post_task",
                  hook_name="tests", status=TaskStatus.COMPLETED,
                  started_at=_at(300), completed_at=_at(340)),
        Execution(job_id="j1", task_id="task-1", attempt=0, execution_type="post_task",
                  hook_name="review-implementation", status=TaskStatus.COMPLETED,
                  started_at=_at(340), completed_at=_at(400)),
    ]

    timeline = _grouped_timeline(_hooked_plan(), tasks, execs,
                                 {"status": JobStatus.COMPLETED, "started_at": _at(0),
                                  "completed_at": _at(400)}, 500)

    [group] = timeline["groups"]
    assert [(s["kind"], s["attempt"]) for s in group["steps"]] == [
        ("task", 1), ("task", 2), ("hook", None), ("hook", None)]
    assert group["estimate_secs"] == (75 + 3 + 8) * 60  # not (75 * 2 + 3 + 8)
    assert group["duration"] == 400.0
    assert timeline["planned_secs"] == (75 + 3 + 8) * 60


def test_job_timeline_plan_hooks_form_before_and_after_groups():
    """c) plan-level hooks land in their own groups, around the tasks."""
    from minimise.models import Execution, JobStatus, TaskStatus

    plan = _timeline_plan().model_dump()
    plan["post_hooks"] = [
        {"name": "review-all", "shell": "true", "estimated_duration_min": 4},
        {"name": "notify", "shell": "true", "estimated_duration_min": 1},
    ]
    tasks = [_task("task-1", "Build", 10, TaskStatus.COMPLETED),
             _task("task-2", "Docs", 20, TaskStatus.COMPLETED)]
    execs = [
        Execution(job_id="j1", task_id=None, attempt=0, execution_type="pre_plan",
                  hook_name="review-plan", status=TaskStatus.COMPLETED,
                  started_at=_at(0), completed_at=_at(60)),
        Execution(job_id="j1", task_id=None, attempt=0, execution_type="post_plan",
                  hook_name="review-all", status=TaskStatus.COMPLETED,
                  started_at=_at(900), completed_at=_at(960)),
        Execution(job_id="j1", task_id=None, attempt=0, execution_type="post_plan",
                  hook_name="notify", status=TaskStatus.FAILED,
                  started_at=_at(960), completed_at=_at(970), exit_reason="exit_1"),
    ]

    timeline = _grouped_timeline(plan, tasks, execs,
                                 {"status": JobStatus.FAILED, "started_at": _at(0),
                                  "completed_at": _at(970)}, 1000)

    groups = timeline["groups"]
    assert [(g["kind"], g["name"], g["task_id"]) for g in groups] == [
        ("plan_hooks", "Before all tasks", None),
        ("task", "Build", "task-1"),
        ("task", "Docs", "task-2"),
        ("plan_hooks", "After all tasks", None),
    ]
    before, after = groups[0], groups[-1]
    assert [s["name"] for s in before["steps"]] == ["review-plan"]
    assert [s["name"] for s in after["steps"]] == ["review-all", "notify"]
    assert (before["status"], before["duration"], before["estimate_secs"]) == (
        "completed", 60.0, 5 * 60)
    assert (after["status"], after["start_offset"], after["duration"], after["estimate_secs"]) == (
        "failed", 900.0, 70.0, 5 * 60)


def test_job_timeline_running_group_has_no_duration():
    """d) a group with a running part reports running and leaves duration to the client."""
    from minimise.models import Execution, JobStatus, TaskStatus

    tasks = [_task("task-1", "Pictures by id", 75, TaskStatus.RUNNING)]
    execs = [
        Execution(job_id="j1", task_id="task-1", attempt=0, status=TaskStatus.COMPLETED,
                  started_at=_at(0), completed_at=_at(600)),
        Execution(job_id="j1", task_id="task-1", attempt=0, execution_type="post_task",
                  hook_name="tests", status=TaskStatus.RUNNING, started_at=_at(600)),
    ]

    timeline = _grouped_timeline(_hooked_plan(), tasks, execs,
                                 {"status": JobStatus.RUNNING, "started_at": _at(0)}, 630)

    [group] = timeline["groups"]
    assert group["status"] == "running"
    assert group["start_offset"] == 0.0
    assert group["duration"] is None
    assert [s["status"] for s in group["steps"]] == ["completed", "running", "pending"]


def test_job_timeline_parts_carry_plan_timeouts():
    """e) timeout_secs comes from the plan's timeout_min, None when unset."""
    from minimise.models import JobStatus

    timeline = _grouped_timeline(_hooked_plan(), [_task("task-1", "Pictures by id", 75)], [],
                                 {"status": JobStatus.PENDING}, 0)

    [group] = timeline["groups"]
    assert [(s["name"], s["timeout_secs"]) for s in group["steps"]] == [
        ("Pictures by id", 150 * 60), ("tests", 10 * 60), ("review-implementation", None),
    ]


def test_build_steps_and_fallback_carry_timeouts():
    from minimise.interfaces.timeline import build_steps, steps_from_executions
    from minimise.models import Plan

    plan = Plan.model_validate(_hooked_plan())
    steps = build_steps(plan, [_task("task-1", "Pictures by id", 75)], [])
    assert [s.timeout for s in steps] == [150, 10, None]

    fallback = steps_from_executions([_task("task-1", "P", 75, timeout_min=90)], [])
    assert [s.timeout for s in fallback] == [90]


def test_get_job_timeline_fallback_groups_parts_by_task(client, mock_job_controller, db):
    """f) the unparseable-plan fallback still groups every part under its task."""
    from minimise.models import Execution, TaskStatus

    job = _make_job(mock_job_controller)
    task_id = job.tasks[0].id
    (mock_job_controller.store.jobs_dir / job.id / "plan.yaml").write_text("name: [invalid")
    for ex in (
        Execution(job_id=job.id, task_id=None, attempt=0, execution_type="pre_plan",
                  hook_name="review-plan", status=TaskStatus.COMPLETED,
                  started_at=_at(0), completed_at=_at(60)),
        Execution(job_id=job.id, task_id=task_id, attempt=0, status=TaskStatus.COMPLETED,
                  started_at=_at(60), completed_at=_at(300)),
        Execution(job_id=job.id, task_id=task_id, attempt=0, execution_type="post_task",
                  hook_name="tests", status=TaskStatus.COMPLETED,
                  started_at=_at(300), completed_at=_at(330)),
        Execution(job_id=job.id, task_id=None, attempt=0, execution_type="post_plan",
                  hook_name="review-all", status=TaskStatus.COMPLETED,
                  started_at=_at(330), completed_at=_at(400)),
    ):
        db.save_execution(ex)

    resp = client.get(f"/jobs/{job.id}")

    assert resp.status_code == 200
    groups = resp.get_json()["timeline"]["groups"]
    assert [(g["kind"], g["name"], g["task_id"]) for g in groups] == [
        ("plan_hooks", "Before all tasks", None),
        ("task", "T1", task_id),
        ("plan_hooks", "After all tasks", None),
    ]
    assert [(s["name"], s["kind"]) for s in groups[1]["steps"]] == [("T1", "task"), ("tests", "hook")]
    assert groups[1]["duration"] == 270.0
    assert groups[1]["estimate_secs"] == 5 * 60  # fallback hooks have no estimate
    assert all(s["timeout_secs"] is None for g in groups for s in g["steps"])


def test_job_timeline_resumed_job_measures_from_its_earliest_step():
    """h) started_at resets on resume: earlier steps never get negative offsets."""
    from minimise.models import Execution, JobStatus, TaskStatus

    tasks = [_task("task-1", "Build", 10, TaskStatus.RUNNING),
             _task("task-2", "Docs", 20)]
    execs = [  # review-plan ran in the first run; the job was resumed at 600s
        Execution(job_id="j1", task_id=None, attempt=0, execution_type="pre_plan",
                  hook_name="review-plan", status=TaskStatus.COMPLETED,
                  started_at=_at(0), completed_at=_at(240)),
        Execution(job_id="j1", task_id="task-1", attempt=0, status=TaskStatus.RUNNING,
                  started_at=_at(610)),
    ]

    timeline = _grouped_timeline(_timeline_plan().model_dump(), tasks, execs,
                                 {"status": JobStatus.RUNNING, "started_at": _at(600)}, 700)

    steps = [s for g in timeline["groups"] for s in g["steps"]]
    assert all(s["start_offset"] >= 0 for s in steps if s["start_offset"] is not None)
    assert all(g["start_offset"] >= 0 for g in timeline["groups"] if g["start_offset"] is not None)
    assert timeline["run_start_offset"] == 600.0  # job.started_at - origin
    assert timeline["now_offset"] == 700.0
    review, build = steps[0], steps[1]
    assert review["start_offset"] == 0.0 and build["start_offset"] == 610.0
    assert review["bar"] == {"start": 0.0, "actual_end": 240.0, "projected_end": 240.0}
    assert build["bar"]["start"] == 610.0 >= review["bar"]["projected_end"]
