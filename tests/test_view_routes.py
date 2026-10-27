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


def test_loop_detail_page_renders_timeline_table_and_summary(client, api_server):
    """The step table became a timeline that loop.js fills from the API."""
    import re

    loop = _make_loop(api_server)

    html = client.get(f"/loops/{loop.loop_id}/view").get_data(as_text=True)

    table = html[html.index('<table class="timeline-table">'):html.index("</table>")]
    assert re.findall(r"<th>(\w+)", table) == ["Step", "Status", "Duration", "Tokens", "Timeline"]
    assert '<tbody id="loop-step-rows">' in table
    assert '<td colspan="5" class="empty">Loading timeline…</td>' in table
    assert "loop-step-table" not in html and ">Retries<" not in html
    summary = html[html.index('class="live-summary"'):html.index('<div class="table-wrap">')]
    assert re.findall(r"<span>([^<]+)</span>", summary) == [
        "Iteration", "Latest stage", "Plan version", "Elapsed", "Tokens", "Avg / iteration",
    ]
    assert 'id="loop-avg-iteration"' in summary and 'id="loop-avg-iteration-note"' in summary
    assert f'<time datetime="{loop.created_at.isoformat()}Z" data-local-time>' in html


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point persona loading at an empty tmp config dir (built-ins only)."""
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setattr("minimise.interfaces.cli.CONFIG_DIR", path)
    return path


def _make_prompt_loop(api_server, plan=None, implement=None, dimension=None):
    from minimise.models import LoopSpec

    spec = LoopSpec.model_validate({
        "version": "1",
        "name": "Prompt previews",
        "goal": "Show every worker's prompt.",
        "max_iterations": 2,
        "loop": {
            "plan": plan or {"prompt": "Inline plan prompt."},
            "implement": implement or {"prompt": "Inline implement prompt."},
            "evaluate": {"dimensions": [{
                "name": "clarity",
                "rubric": "Is it clear?",
                **(dimension or {"prompt": "Inline evaluate prompt."}),
            }]},
        },
    })
    return api_server.loop_store.create(spec, plan_path="/tmp/loop.yaml")


def _stage_blocks(html):
    """Split the loop spec into its plan, implement and evaluate cards."""
    plan_at = html.index("<h4>Plan</h4>")
    implement_at = html.index("<h4>Implement</h4>")
    evaluate_at = html.index("<h4>Evaluate</h4>")
    end = html.index('<footer class="loop-spec-footer">')
    return {
        "plan": html[plan_at:implement_at],
        "implement": html[implement_at:evaluate_at],
        "evaluate": html[evaluate_at:end],
    }


def _get_loop_page(client, loop):
    response = client.get(f"/loops/{loop.loop_id}/view")
    assert response.status_code == 200
    return _stage_blocks(response.get_data(as_text=True))


def test_loop_detail_page_folds_prompt_file_collapsed(client, api_server, tmp_path):
    import re

    path = tmp_path / "planner.md"
    path.write_text("# Planner heading\n\nPlan carefully.\n")
    loop = _make_prompt_loop(api_server, plan={"prompt_file": str(path)})

    plan = _get_loop_page(client, loop)["plan"]

    assert re.findall(r"<details[^>]*>", plan) == ['<details class="prompt-fold">']
    assert "<h1>Planner heading</h1>" in plan
    assert f"<code>{path}</code>" in plan
    assert '<span class="prompt-fold-label">Prompt file</span>' in plan
    assert '<span class="prompt-fold-name">planner.md</span>' in plan
    assert '<span class="prompt-fold-size">' in plan


def test_loop_detail_page_folds_persona_prompt_with_link(client, api_server, config_dir):
    from minimise.interfaces.prompt_preview import render_prompt_markdown
    from minimise.personas import load_builtin_personas

    name = "mini:doc-review:clarity"
    loop = _make_prompt_loop(api_server, dimension={"persona": name})

    evaluate = _get_loop_page(client, loop)["evaluate"]

    expected = render_prompt_markdown(load_builtin_personas()[name].system_prompt)
    assert '<details class="prompt-fold">' in evaluate
    assert '<span class="prompt-fold-label">Persona prompt</span>' in evaluate
    assert expected in evaluate
    assert f'href="/personas/{name}">Open persona page</a>' in evaluate


def test_loop_detail_page_folds_built_in_default(client, api_server):
    loop = _make_prompt_loop(api_server, implement={"harness": "codex"})

    implement = _get_loop_page(client, loop)["implement"]

    assert '<details class="prompt-fold">' in implement
    assert '<span class="prompt-fold-label">Built-in default prompt</span>' in implement
    assert '<span class="prompt-fold-name">implement</span>' in implement
    assert "You are the IMPLEMENTER" in implement


def test_loop_detail_page_inline_prompt_has_no_fold(client, api_server):
    loop = _make_prompt_loop(api_server, plan={"prompt": "Choose the next gap."})

    plan = _get_loop_page(client, loop)["plan"]

    assert "<span>Prompt</span>\n        <p>Choose the next gap.</p>" in plan
    assert "prompt-fold" not in plan


@pytest.mark.parametrize("kind, notice", [
    ("missing", "Prompt file not found: "),
    ("relative", "Relative path: the loop&#39;s working directory isn&#39;t recorded"),
    ("too_large", "Prompt file is too large to preview (300 KB)."),
    ("unknown_persona", "Unknown persona &#39;ghost&#39;"),
])
def test_loop_detail_page_shows_notice_for_bad_prompt_sources(
    client, api_server, config_dir, tmp_path, kind, notice
):
    big = tmp_path / "big.md"
    big.write_bytes(b"a" * (300 * 1024))
    worker = {
        "missing": {"prompt_file": str(tmp_path / "gone.md")},
        "relative": {"prompt_file": "prompts/implement.md"},
        "too_large": {"prompt_file": str(big)},
        "unknown_persona": {"persona": "ghost"},
    }[kind]
    loop = _make_prompt_loop(api_server, dimension=worker)

    evaluate = _get_loop_page(client, loop)["evaluate"]

    assert '<details class="prompt-fold">' in evaluate
    assert '<p class="prompt-fold-error" role="status">' in evaluate
    assert notice in evaluate
    assert "prompt-fold-body" not in evaluate


def test_loop_detail_page_survives_malformed_personas_yaml(
    client, api_server, config_dir, tmp_path
):
    (config_dir / "personas.yaml").write_text("writer: [unclosed\n")
    path = tmp_path / "planner.md"
    path.write_text("# Planner heading")
    loop = _make_prompt_loop(
        api_server,
        plan={"prompt_file": str(path)},
        dimension={"persona": "mini:doc-review:clarity"},
    )

    blocks = _get_loop_page(client, loop)

    assert "Personas could not be loaded: " in blocks["evaluate"]
    assert '<p class="prompt-fold-error" role="status">' in blocks["evaluate"]
    assert "<h1>Planner heading</h1>" in blocks["plan"]


def test_detail_css_styles_prompt_fold_with_theme_variables():
    import re
    from pathlib import Path
    import minimise.interfaces as interfaces

    css = (Path(interfaces.__file__).parent / "static" / "detail.css").read_text()
    rules = re.findall(r"(\.prompt-fold[^{]*)\{([^}]*)\}", css)

    assert rules
    declarations = "".join(body for _, body in rules)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", declarations)
    body = next(b for selector, b in rules if selector.strip() == ".prompt-fold-body")
    assert "max-height: 32rem" in body
    assert "overflow: auto" in body


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


def _make_assigned_job(mock_job_controller, *assignees):
    """A job with one task per assignee (None = no assignee), ids t1, t2, ..."""
    from minimise.models import Plan

    tasks = [{
        "id": f"t{index}", "name": f"Task {index}", "description": "d", "goal": "g",
        "estimated_duration_min": 5,
        "post_hooks": [{"name": "tests", "shell": "true", "estimated_duration_min": 3}],
        **({"assignee": assignee} if assignee else {}),
    } for index, assignee in enumerate(assignees, start=1)]
    plan = Plan.model_validate({"name": "Assigned", "tasks": tasks})
    return mock_job_controller.store.create(plan, base_commit="abc123", plan_path="/tmp/plan.yaml")


def _task_blocks(client, job):
    """Split the job page's plan into one HTML block per task card."""
    response = client.get(f"/jobs/{job.id}/view")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Not "</ol>": a rendered persona prompt can hold its own ordered list.
    end = html.index('<details class="plan-raw">')
    return html[:end].split('<li class="plan-task-item">')[1:]


def test_job_detail_page_folds_assignee_persona_prompt(client, mock_job_controller, config_dir):
    import re
    from minimise.interfaces.prompt_preview import render_prompt_markdown
    from minimise.personas import load_builtin_personas

    name = "mini:doc-review:clarity"
    job = _make_assigned_job(mock_job_controller, name, None)

    assigned, unassigned = _task_blocks(client, job)

    expected = render_prompt_markdown(load_builtin_personas()[name].system_prompt)
    assert re.findall(r'<details class="prompt-fold[^>]*>', assigned) == ['<details class="prompt-fold">']
    assert '<span class="prompt-fold-label">Persona prompt</span>' in assigned
    assert f'<span class="prompt-fold-name">{name}</span>' in assigned
    assert expected in assigned
    assert f'href="/personas/{name}">Open persona page</a>' in assigned
    assert f"Persona · {name}" in assigned
    fold_at = assigned.index('<details class="prompt-fold">')
    assert assigned.index("<h5>Instructions</h5>") < fold_at < assigned.index("<h5>After this task</h5>")
    assert "prompt-fold" not in unassigned


def test_job_detail_page_folds_user_persona_prompt(client, mock_job_controller, config_dir):
    (config_dir / "personas.yaml").write_text(
        "migration-owner:\n  prompt: |\n    # Owner brief\n\n    Ship **safely**.\n"
    )
    job = _make_assigned_job(mock_job_controller, "migration-owner")

    (task,) = _task_blocks(client, job)

    assert '<details class="prompt-fold">' in task
    assert "<h1>Owner brief</h1>" in task
    assert "<strong>safely</strong>" in task
    assert 'href="/personas/migration-owner">Open persona page</a>' in task


def test_job_detail_page_shows_notice_for_unknown_assignee(client, mock_job_controller, config_dir):
    job = _make_assigned_job(mock_job_controller, "ghost")

    (task,) = _task_blocks(client, job)

    assert '<details class="prompt-fold">' in task
    assert '<p class="prompt-fold-error" role="status">' in task
    assert "Unknown persona &#39;ghost&#39;" in task
    assert "prompt-fold-body" not in task


def test_job_detail_page_survives_malformed_personas_yaml(client, mock_job_controller, config_dir):
    (config_dir / "personas.yaml").write_text("writer: [unclosed\n")
    job = _make_assigned_job(mock_job_controller, "mini:doc-review:clarity", "writer", None)

    first, second, unassigned = _task_blocks(client, job)

    for task in (first, second):
        assert '<p class="prompt-fold-error" role="status">' in task
        assert "Personas could not be loaded: " in task
    assert "prompt-fold" not in unassigned


def test_prompt_fold_markup_lives_only_in_its_macro():
    from pathlib import Path
    import minimise.interfaces as interfaces

    templates = Path(interfaces.__file__).parent / "templates"
    with_markup = sorted(path.name for path in templates.glob("*.html")
                         if 'class="prompt-fold' in path.read_text())
    assert with_markup == ["_prompt_fold.html"]
    for name in ("_loop_spec_detail.html", "_plan_detail.html"):
        source = (templates / name).read_text()
        assert '{% from "_prompt_fold.html" import render_prompt_fold %}' in source
        assert "render_prompt_fold(" in source.split("import render_prompt_fold", 1)[1]


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


def _loop_step(step_id, iteration, step_type, start, end=None, status=None, dimension=None):
    """A loop step that started ``start`` secs and ended ``end`` secs after _at(0)."""
    from minimise.models import LoopStep, TaskStatus
    return LoopStep(
        step_id=step_id, loop_id="loop-1", iteration=iteration, step_type=step_type,
        dimension=dimension,
        status=status or (TaskStatus.COMPLETED if end is not None else TaskStatus.RUNNING),
        started_at=_at(start) if start is not None else None,
        completed_at=_at(end) if end is not None else None,
    )


def _timed_loop(status, started=None, completed=None):
    from minimise.models import Loop
    return Loop(loop_id="loop-1", name="timed", status=status,
                started_at=_at(started) if started is not None else None,
                completed_at=_at(completed) if completed is not None else None)


def test_loop_timeline_iteration_spans_parallel_evaluators():
    """a) evaluators overlap (loop-4adaca): wall time from first start to last end."""
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus

    steps = [
        _loop_step("plan", 1, "plan", 0, 20),
        _loop_step("impl", 1, "implement", 20, 80),
        _loop_step("eval-a", 1, "evaluate", 80, 111.6, dimension="clarity"),
        _loop_step("eval-b", 1, "evaluate", 80, 202, dimension="accuracy"),
    ]

    timeline = _loop_timeline(_timed_loop(JobStatus.COMPLETED, 0, 205), steps, _at(999))

    [group] = timeline["groups"]
    assert group["iteration"] == 1 and group["status"] == "completed"
    assert group["start_offset"] == 0.0
    assert group["duration"] == 202.0  # not the sum, 233.6
    assert sum(s["duration"] for s in group["steps"]) == pytest.approx(233.6)
    eval_a = group["steps"][2]
    assert eval_a["dimension"] == "clarity" and eval_a["stage"] == "Evaluate"
    assert eval_a["start_offset"] == 80.0 and eval_a["duration"] == 31.6
    assert eval_a["bar"] == {"start": 80.0, "end": 111.6}
    assert timeline["now_offset"] == 205.0  # a finished loop ends at completed_at
    assert timeline["run_start_offset"] == 0.0
    assert timeline["total_secs"] == 205.0
    assert timeline["avg_iteration_secs"] == 202.0


def test_loop_timeline_resumed_loop_measures_from_its_earliest_step():
    """b) the first Plan attempt started before loop.started_at (loop-4adaca)."""
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus, TaskStatus

    steps = [  # 15:58:20 first run; resumed at 16:01:59 (+219s)
        _loop_step("plan-0", 1, "plan", 0, 60, TaskStatus.FAILED),
        _loop_step("plan-1", 1, "plan", 225, 260),
        _loop_step("impl", 1, "implement", 260),
    ]

    timeline = _loop_timeline(_timed_loop(JobStatus.RUNNING, 219), steps, _at(400))

    parts = timeline["groups"][0]["steps"]
    assert all(s["start_offset"] >= 0 for s in parts)
    assert [s["start_offset"] for s in parts] == [0.0, 225.0, 260.0]
    assert timeline["groups"][0]["start_offset"] == 0.0
    assert timeline["run_start_offset"] == 219.0  # loop.started_at - origin
    assert timeline["now_offset"] == 400.0


def test_loop_timeline_running_step_ticks_to_now():
    """c) a running step and its iteration have no fixed duration."""
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus

    steps = [
        _loop_step("plan-1", 1, "plan", 0, 30),
        _loop_step("impl-1", 1, "implement", 30, 100),
        _loop_step("plan-2", 2, "plan", 100, 120),
        _loop_step("impl-2", 2, "implement", 120),
    ]

    timeline = _loop_timeline(_timed_loop(JobStatus.RUNNING, 0), steps, _at(300))

    first, second = timeline["groups"]
    running = second["steps"][1]
    assert running["status"] == "running"
    assert running["duration"] is None
    assert running["bar"] == {"start": 120.0, "end": 300.0}
    assert running["bar"]["end"] == timeline["now_offset"]
    assert second["status"] == "running" and second["duration"] is None
    assert second["start_offset"] == 100.0
    assert first["status"] == "completed" and first["duration"] == 100.0
    assert timeline["total_secs"] == 300.0


def test_loop_timeline_averages_only_finished_iterations():
    """d) the running iteration stays out of the average."""
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus

    steps = [
        _loop_step("plan-1", 1, "plan", 0, 100),
        _loop_step("plan-2", 2, "plan", 100, 300),
        _loop_step("plan-3", 3, "plan", 300),
    ]

    timeline = _loop_timeline(_timed_loop(JobStatus.RUNNING, 0), steps, _at(1000))

    assert [g["duration"] for g in timeline["groups"]] == [100.0, 200.0, None]
    assert timeline["avg_iteration_secs"] == 150.0  # not (100 + 200 + 700) / 3


def test_loop_timeline_iteration_status_follows_its_last_step():
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus, TaskStatus

    steps = [
        _loop_step("plan-1", 1, "plan", 0, 10, TaskStatus.FAILED),  # retried below
        _loop_step("plan-1b", 1, "plan", 10, 20),
        _loop_step("plan-2", 2, "plan", 20, 30),
        _loop_step("impl-2", 2, "implement", 30, 50, TaskStatus.FAILED),
        _loop_step("plan-3", 3, "plan", None, status=TaskStatus.PENDING),
    ]

    timeline = _loop_timeline(_timed_loop(JobStatus.FAILED, 0, 50), steps, _at(999))

    assert [g["status"] for g in timeline["groups"]] == ["completed", "failed", "pending"]
    assert timeline["groups"][1]["duration"] == 30.0
    assert timeline["avg_iteration_secs"] == 25.0


def test_loop_timeline_iteration_stopped_by_its_last_step_until_resumed():
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus, TaskStatus

    stopped = [
        _loop_step("plan-1", 1, "plan", 0, 10),
        _loop_step("eval-a", 1, "evaluate", 10, 20),
        _loop_step("eval-b", 1, "evaluate", 20, 21, TaskStatus.STOPPED),  # hit the usage limit
    ]
    timeline = _loop_timeline(_timed_loop(JobStatus.STOPPED, 0, 21), stopped, _at(999))
    assert [g["status"] for g in timeline["groups"]] == ["stopped"]

    resumed = stopped + [_loop_step("eval-b2", 1, "evaluate", 100, 110)]
    timeline = _loop_timeline(_timed_loop(JobStatus.COMPLETED, 100, 110), resumed, _at(999))
    assert [g["status"] for g in timeline["groups"]] == ["completed"]


def test_loop_timeline_unstarted_loop_has_no_origin():
    """e) nothing started: no offsets and no bars."""
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus, TaskStatus

    steps = [_loop_step("plan-1", 1, "plan", None, status=TaskStatus.PENDING)]

    timeline = _loop_timeline(_timed_loop(JobStatus.PENDING), steps, _at(500))

    assert timeline["now_offset"] is None
    assert timeline["run_start_offset"] is None
    assert timeline["avg_iteration_secs"] is None
    assert timeline["total_secs"] == 1
    [group] = timeline["groups"]
    assert group["status"] == "pending"
    assert group["start_offset"] is None and group["duration"] is None
    assert all(s["bar"] is None and s["start_offset"] is None for s in group["steps"])
    assert _loop_timeline(_timed_loop(JobStatus.PENDING), [], _at(500))["groups"] == []


def test_get_loop_api_includes_timeline_and_list_is_unchanged(client, api_server, db):
    """f) the detail API gains "timeline"; the list API gains only tokens_total."""
    from minimise.models import JobStatus, LoopStep, TaskStatus

    loop = _make_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(8))
    db.create_loop_step(LoopStep(
        step_id="step-plan", loop_id=loop.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.COMPLETED, started_at=_at(0), completed_at=_at(2),
    ))

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    assert {"loop_id", "name", "status", "iteration", "max_iterations", "stage",
            "plan_version", "evaluator_count", "created_at", "started_at",
            "completed_at", "elapsed", "steps", "timeline"} <= set(data)
    assert data["elapsed"] == "8.0s"
    assert data["steps"][0]["duration"] == "2.0s"  # the old step list keeps its labels
    [group] = data["timeline"]["groups"]
    assert group["iteration"] == 1 and group["duration"] == 2.0
    assert group["steps"][0]["bar"] == {"start": 0.0, "end": 2.0}
    assert data["timeline"]["now_offset"] == 8.0

    [listed] = client.get("/api/loops").get_json()
    assert set(listed) == {"loop_id", "name", "status", "iteration", "max_iterations",
                           "stage", "plan_version", "evaluator_count", "created_at",
                           "started_at", "completed_at", "elapsed", "tokens_total"}


def test_loop_api_steps_share_the_clis_step_shape(client, api_server, db, seed_run):
    """The web's steps are loop_step_json (the shape `mini loop status --format json`
    prints) plus the web's own keys, so the two can't drift."""
    from minimise.interfaces.formatting import loop_step_json
    from minimise.models import JobStatus, LoopStep, TaskStatus, TokenUsage

    loop = _make_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(8))
    db.create_loop_step(LoopStep(
        step_id="step-plan", loop_id=loop.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.COMPLETED, started_at=_at(0), completed_at=_at(2),
    ))
    seed_run(db, TokenUsage(1, 2, 3, 4, reasoning=5, by_model={"claude-opus-5-5": {"input": 1}}),
             loop_id=loop.loop_id, step_id="step-plan")
    db.create_loop_step(LoopStep(
        step_id="step-impl", loop_id=loop.loop_id, iteration=1, step_type="implement",
        status=TaskStatus.COMPLETED, started_at=_at(2), completed_at=_at(4),
    ))
    seed_run(db, None, loop_id=loop.loop_id, step_id="step-impl")

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    loaded = api_server.loop_store.load(loop.loop_id)
    runs = db.list_agent_runs_for_loop(loop.loop_id)
    for step, served in zip(db.list_loop_steps(loop.loop_id), data["steps"]):
        assert loop_step_json(loaded, step, runs).items() <= served.items()
    assert data["steps"][0]["tokens"] == {"input": 1, "cache_read": 2, "cache_write": 3,
                                          "output": 4, "total": 10}
    assert data["steps"][1]["tokens"] is None
    body = client.get(f"/api/loops/{loop.loop_id}").get_data(as_text=True)
    assert "reasoning" not in body and "by_model" not in body and "claude-opus" not in body


def test_loop_api_shows_a_failed_loops_running_step_as_stopped(client, api_server, db):
    """A dead engine left its implement step RUNNING; a later run re-did it and
    failed at evaluate. Nothing runs that orphan, so it reads as stopped."""
    from minimise.models import JobStatus, LoopStep, TaskStatus

    loop = _make_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.FAILED,
                          started_at=_at(100), completed_at=_at(120))
    for step_id, step_type, dim, status, start, end in (
        ("step-plan", "plan", None, TaskStatus.COMPLETED, 0, 5),
        ("step-orphan", "implement", None, TaskStatus.RUNNING, 5, None),
        ("step-impl", "implement", None, TaskStatus.COMPLETED, 100, 110),
        ("step-eval", "evaluate", "clarity", TaskStatus.FAILED, 110, 120),
    ):
        db.create_loop_step(LoopStep(
            step_id=step_id, loop_id=loop.loop_id, iteration=1, step_type=step_type,
            dimension=dim, status=status, started_at=_at(start),
            completed_at=_at(end) if end is not None else None,
        ))

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    assert data["stage"] == "Evaluate / clarity"  # not the orphaned "Implement"
    orphan = next(s for s in data["steps"] if s["step_id"] == "step-orphan")
    assert orphan["status"] == "stopped" and orphan["duration"] == "\u2014"
    [group] = data["timeline"]["groups"]
    assert group["status"] == "failed" and group["duration"] == 120.0
    timed = next(s for s in group["steps"] if s["step_id"] == "step-orphan")
    assert timed["status"] == "stopped"
    assert "running" not in {s["status"] for s in group["steps"]}
    # A loop `mini loop stop` left STOPPED before steps were stopped with it: same.
    db.update_loop_status(loop.loop_id, status=JobStatus.STOPPED)
    [group] = client.get(f"/api/loops/{loop.loop_id}").get_json()["timeline"]["groups"]
    assert group["status"] == "failed"
    assert "running" not in {s["status"] for s in group["steps"]}

    html = client.get(f"/loops/{loop.loop_id}/view").get_data(as_text=True)
    assert '<strong id="loop-stage">Evaluate / clarity</strong>' in html
    # The DB row is untouched: only reconcile, stop and the engine repair rows.
    assert next(s for s in db.list_loop_steps(loop.loop_id)
                if s.step_id == "step-orphan").status == TaskStatus.RUNNING


def test_loop_api_keeps_a_running_loops_running_step_running(client, api_server, db, monkeypatch):
    from minimise.models import JobStatus, LoopStep, TaskStatus

    monkeypatch.setattr("minimise.storage.loop_store._pid_alive", lambda _pid: True)
    loop = _make_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.RUNNING, started_at=_at(0), pid=1)
    db.create_loop_step(LoopStep(
        step_id="step-plan", loop_id=loop.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.RUNNING, started_at=_at(0),
    ))

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    assert data["steps"][0]["status"] == "running"
    assert data["timeline"]["groups"][0]["status"] == "running"


def test_loop_page_and_api_show_the_usage_limit_stop_reason(client, api_server, db):
    from minimise.models import JobStatus

    loop = _make_loop(api_server)
    reason = "usage limit: resets 4:40am (America/Los_Angeles)"
    db.update_loop_status(loop.loop_id, status=JobStatus.STOPPED, stop_reason=reason)

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()
    assert data["status"] == "stopped" and data["stop_reason"] == reason

    html = client.get(f"/loops/{loop.loop_id}/view").get_data(as_text=True)
    assert f'<p id="loop-stop-reason" class="detail-meta">Stopped: {reason}</p>' in html


def test_loop_page_hides_the_stop_reason_when_there_is_none(client, api_server):
    loop = _make_loop(api_server)

    assert client.get(f"/api/loops/{loop.loop_id}").get_json()["stop_reason"] is None
    html = client.get(f"/loops/{loop.loop_id}/view").get_data(as_text=True)
    assert '<p id="loop-stop-reason" class="detail-meta" hidden></p>' in html


def _verdict(step_id, verdict):
    """An evaluate journal record as the engine stamps it."""
    return {"step_type": "evaluate", "step_id": step_id, "verdict": verdict, "control": "done"}


def _verdict_timeline(steps, records, dimension_count=0):
    from minimise.interfaces.loop_views import _loop_timeline
    from minimise.models import JobStatus
    return _loop_timeline(
        _timed_loop(JobStatus.RUNNING, 0), steps, _at(500),
        journal_records=records, dimension_count=dimension_count,
    )


def test_loop_timeline_evaluate_steps_carry_journaled_verdicts():
    steps = [
        _loop_step("plan", 1, "plan", 0, 10),
        _loop_step("impl", 1, "implement", 10, 20),
        _loop_step("eval-a", 1, "evaluate", 20, 30, dimension="clarity"),
        _loop_step("eval-b", 1, "evaluate", 20, 40, dimension="accuracy"),
        _loop_step("eval-c", 1, "evaluate", 20, 50, dimension="tone"),
    ]
    records = [
        _verdict("eval-a", "fail"),  # an earlier attempt: the last record wins
        _verdict("eval-a", " PASS "),
        _verdict("eval-b", "fail"),
        _verdict("eval-c", "Blocked"),
    ]

    [group] = _verdict_timeline(steps, records)["groups"]

    assert [s["verdict"] for s in group["steps"]] == [None, None, "pass", "fail", "blocked"]


def test_loop_timeline_ignores_unknown_verdicts_and_non_evaluate_steps():
    steps = [
        _loop_step("plan", 1, "plan", 0, 10),
        _loop_step("impl", 1, "implement", 10, 20),
        _loop_step("eval-a", 1, "evaluate", 20, 30, dimension="clarity"),
        _loop_step("eval-b", 1, "evaluate", 20, 30, dimension="accuracy"),
        _loop_step("eval-c", 1, "evaluate", 20, 30, dimension="tone"),
    ]
    records = [
        {"step_type": "plan", "step_id": "plan", "verdict": "pass"},
        {"step_type": "implement", "step_id": "impl", "verdict": "pass"},
        _verdict("impl", "pass"),  # mislabeled: impl is still not an evaluate step
        _verdict("eval-a", "maybe"),
        {"step_type": "evaluate", "step_id": "eval-b", "control": "done"},  # no verdict
        _verdict("eval-c", 3),  # not a string
    ]

    [group] = _verdict_timeline(steps, records)["groups"]

    assert [s["verdict"] for s in group["steps"]] == [None] * 5
    assert group["passing"] is None


def test_loop_timeline_running_evaluator_has_no_verdict_yet():
    steps = [_loop_step("eval-a", 1, "evaluate", 0, dimension="clarity")]

    [group] = _verdict_timeline(steps, [_verdict("eval-a", "pass")])["groups"]

    [step] = group["steps"]
    assert step["status"] == "running" and step["verdict"] is None
    assert group["passing"] is None


def test_loop_timeline_matches_records_without_step_id_by_iteration_and_dimension():
    steps = [
        _loop_step("eval-1", 1, "evaluate", 0, 10, dimension="clarity"),
        _loop_step("eval-2a", 2, "evaluate", 10, 20, dimension="clarity"),
        _loop_step("eval-2b", 2, "evaluate", 10, 20, dimension="accuracy"),
    ]
    records = [
        {"step_type": "evaluate", "iteration": 2, "dimension": "clarity", "verdict": "pass"},
        {"step_type": "evaluate", "iteration": 1, "dimension": "accuracy", "verdict": "fail"},
    ]

    first, second = _verdict_timeline(steps, records)["groups"]

    assert [s["verdict"] for s in first["steps"]] == [None]
    assert [s["verdict"] for s in second["steps"]] == ["pass", None]


def test_loop_timeline_iteration_passing_counts_verdicts_out_of_all_dimensions():
    steps = [
        _loop_step("plan-1", 1, "plan", 0, 10),
        _loop_step("eval-1a", 1, "evaluate", 10, 20, dimension="clarity"),
        _loop_step("eval-1b", 1, "evaluate", 10, 20, dimension="accuracy"),
        _loop_step("eval-1c", 1, "evaluate", 10, 20, dimension="tone"),
        _loop_step("plan-2", 2, "plan", 20, 30),
        _loop_step("eval-2a", 2, "evaluate", 30, 40, dimension="clarity"),
        _loop_step("eval-2b", 2, "evaluate", 30, 40, dimension="accuracy"),
        _loop_step("plan-3", 3, "plan", 40, 50),
    ]
    records = [
        _verdict("eval-1a", "pass"),
        _verdict("eval-1b", "fail"),
        _verdict("eval-1c", "blocked"),
        _verdict("eval-2a", "pass"),
    ]

    first, second, third = _verdict_timeline(steps, records, dimension_count=3)["groups"]
    assert first["passing"] == {"passed": 1, "total": 3, "carried": 0}
    assert second["passing"] == {"passed": 1, "total": 3, "carried": 0}
    assert third["passing"] is None  # no verdicts, no sub-line

    _, second = _verdict_timeline(steps[:7], records, dimension_count=7)["groups"]
    assert second["passing"] == {"passed": 1, "total": 7, "carried": 0}  # 2 evaluate steps, 7 dimensions

    first, _ = _verdict_timeline(steps[:7], records)["groups"]
    assert first["passing"] == {"passed": 1, "total": 3, "carried": 0}  # no spec: count the steps' dimensions


def test_loop_timeline_rerun_evaluator_counts_once_by_its_latest_verdict():
    from minimise.models import TaskStatus

    steps = [
        _loop_step("eval-old", 1, "evaluate", 0, 10, TaskStatus.FAILED, dimension="clarity"),
        _loop_step("eval-new", 1, "evaluate", 20, 30, dimension="clarity"),
    ]
    records = [_verdict("eval-old", "pass"), _verdict("eval-new", "fail")]

    [group] = _verdict_timeline(steps, records, dimension_count=1)["groups"]

    assert group["passing"] == {"passed": 0, "total": 1, "carried": 0}


def test_get_loop_api_serves_evaluate_verdicts_from_the_journal(client, api_server, db):
    from minimise.models import JobStatus, LoopStep, TaskStatus
    from minimise.orchestration import loop_journal

    loop = _make_loop(api_server)  # one evaluate dimension: clarity
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(8))
    db.create_loop_step(LoopStep(
        step_id="step-plan", loop_id=loop.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.COMPLETED, started_at=_at(0), completed_at=_at(2),
    ))
    db.create_loop_step(LoopStep(
        step_id="step-eval", loop_id=loop.loop_id, iteration=1, step_type="evaluate",
        dimension="clarity", status=TaskStatus.COMPLETED,
        started_at=_at(3), completed_at=_at(5),
    ))
    loop_journal.append(api_server.loop_store.journal_path(loop.loop_id), {
        "step_type": "evaluate", "step_id": "step-eval", "iteration": 1,
        "dimension": "clarity", "verdict": "fail", "score": 5,
        "findings": ["Step 3 skips the install."], "control": "done",
    })

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    [group] = data["timeline"]["groups"]
    verdicts = {s["step_id"]: s["verdict"] for s in group["steps"]}
    assert verdicts == {"step-plan": None, "step-eval": "fail"}
    assert group["passing"] == {"passed": 0, "total": 1, "carried": 0}


def test_loop_journal_is_read_once_by_detail_views_and_never_by_the_list(
    client, api_server, monkeypatch
):
    from minimise.orchestration import loop_journal

    loop = _make_loop(api_server)
    reads = []
    real_read = loop_journal.read
    monkeypatch.setattr(loop_journal, "read", lambda path: reads.append(path) or real_read(path))

    assert client.get("/loops").status_code == 200
    assert client.get("/api/loops").status_code == 200
    assert reads == []

    assert client.get(f"/api/loops/{loop.loop_id}").status_code == 200
    assert len(reads) == 1
    assert client.get(f"/loops/{loop.loop_id}/view").status_code == 200
    assert len(reads) == 2


def test_loop_journal_api_renders_findings_list_as_readable_text(client, api_server):
    """A findings list showed as a JSON array with \\u-escaped Cyrillic."""
    from minimise.orchestration import loop_journal

    loop = _make_loop(api_server)
    loop_journal.append(api_server.loop_store.journal_path(loop.loop_id), {
        "step_type": "evaluate", "step_id": "step-eval", "iteration": 1,
        "dimension": "scene", "verdict": "fail",
        "findings": ["[low] сцена (s1): «Никто, никогда»", "[high] второй пункт"],
    })

    [entry] = client.get(f"/api/loops/{loop.loop_id}/journal").get_json()["records"]

    assert entry["message"] == "[low] сцена (s1): «Никто, никогда»\n\n[high] второй пункт"
    assert "\\u" not in entry["raw"] and "«Никто, никогда»" in entry["raw"]


def _make_select_failed_loop(api_server):
    from minimise.models import LoopSpec

    spec = LoopSpec.model_validate({
        "version": "1",
        "name": "Rerun failed",
        "goal": "Pass every dimension.",
        "max_iterations": 5,
        "loop": {
            "plan": {}, "implement": {},
            "evaluate": {
                "select": "failed",
                "dimensions": [
                    {"name": "clarity", "rubric": "Clear?"},
                    {"name": "accuracy", "rubric": "Accurate?"},
                ],
            },
        },
    })
    return api_server.loop_store.create(spec, plan_path="/tmp/loop.yaml")


def test_get_loop_api_serves_carried_over_passes_and_rechecks(client, api_server, db):
    """select: failed — iteration 2 carries clarity's pass from 1; iteration 3 re-checks it."""
    from minimise.models import JobStatus, LoopStep, TaskStatus
    from minimise.orchestration import loop_journal

    loop = _make_select_failed_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(100))
    jpath = api_server.loop_store.journal_path(loop.loop_id)

    def step(step_id, iteration, step_type, start, end, dimension=None):
        db.create_loop_step(LoopStep(
            step_id=step_id, loop_id=loop.loop_id, iteration=iteration,
            step_type=step_type, dimension=dimension, status=TaskStatus.COMPLETED,
            started_at=_at(start), completed_at=_at(end),
        ))

    def record(step_id, iteration, dimension, verdict, **extra):
        loop_journal.append(jpath, {
            "step_type": "evaluate", "step_id": step_id, "iteration": iteration,
            "dimension": dimension, "verdict": verdict, "control": "done", **extra,
        })

    step("plan-1", 1, "plan", 0, 5)
    step("eval-1c", 1, "evaluate", 10, 20, "clarity")
    step("eval-1a", 1, "evaluate", 10, 25, "accuracy")
    record("eval-1c", 1, "clarity", "pass")
    record("eval-1a", 1, "accuracy", "fail")
    step("eval-2a", 2, "evaluate", 30, 45, "accuracy")
    step("eval-2c", 2, "evaluate", 45, 45, "clarity")  # carried: started_at == completed_at
    record("eval-2a", 2, "accuracy", "fail")
    record("eval-2c", 2, "clarity", "pass", evaluated_in_iteration=1, findings=(
        "Not re-evaluated in iteration 2 (select: failed). "
        "It passed when last evaluated, in iteration 1."))
    step("eval-3a", 3, "evaluate", 50, 60, "accuracy")
    step("eval-3c", 3, "evaluate", 60, 70, "clarity")
    record("eval-3a", 3, "accuracy", "pass")
    record("eval-3c", 3, "clarity", "pass", recheck=True)

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    first, second, third = data["timeline"]["groups"]
    steps = {s["step_id"]: s for g in (first, second, third) for s in g["steps"]}
    carried = steps["eval-2c"]
    assert carried["verdict"] == "pass"
    assert carried["carried_from"] == 1
    assert carried["bar"] is None
    assert carried["recheck"] is False
    assert second["passing"] == {"passed": 1, "total": 2, "carried": 1}

    assert steps["eval-3c"]["recheck"] is True
    assert steps["eval-3c"]["carried_from"] is None
    assert steps["eval-3c"]["bar"] == {"start": 60.0, "end": 70.0}
    for ordinary in ("plan-1", "eval-1c", "eval-1a", "eval-2a", "eval-3a"):
        assert steps[ordinary]["recheck"] is False
        assert steps[ordinary]["carried_from"] is None
        assert steps[ordinary]["bar"] is not None
    assert first["passing"] == {"passed": 1, "total": 2, "carried": 0}
    assert third["passing"] == {"passed": 2, "total": 2, "carried": 0}


def test_loop_timeline_carried_from_ignores_non_integer_values():
    steps = [
        _loop_step("eval-a", 2, "evaluate", 10, 10, dimension="clarity"),
        _loop_step("eval-b", 2, "evaluate", 10, 10, dimension="accuracy"),
    ]
    records = [
        {**_verdict("eval-a", "pass"), "evaluated_in_iteration": "1", "recheck": "yes"},
        {**_verdict("eval-b", "pass"), "evaluated_in_iteration": True},
    ]

    [group] = _verdict_timeline(steps, records)["groups"]

    assert [(s["carried_from"], s["recheck"]) for s in group["steps"]] == [
        (None, False), (None, False),
    ]
    assert group["passing"]["carried"] == 0


def test_loop_detail_page_shows_the_select_figure(client, api_server):
    import re

    def figures(loop):
        html = client.get(f"/loops/{loop.loop_id}/view").get_data(as_text=True)
        return re.findall(r"<div><strong>([^<]+)</strong><span>([^<]+)</span></div>", html)

    assert ("failed", "select") in figures(_make_select_failed_loop(api_server))
    assert ("all", "select") in figures(_make_loop(api_server))  # the default


def test_loop_journal_api_renders_carried_over_findings_as_readable_text(client, api_server):
    from minimise.orchestration import loop_journal

    loop = _make_select_failed_loop(api_server)
    sentence = ("Not re-evaluated in iteration 5 (select: failed). "
                "It passed when last evaluated, in iteration 3.")
    loop_journal.append(api_server.loop_store.journal_path(loop.loop_id), {
        "control": "done", "verdict": "pass", "evaluated_in_iteration": 3,
        "findings": sentence, "timestamp": "2026-10-25T10:00:00",
        "loop_id": loop.loop_id, "step_id": "step-carried", "iteration": 5,
        "step_type": "evaluate", "dimension": "clarity", "plan_version": 1,
    })

    [entry] = client.get(f"/api/loops/{loop.loop_id}/journal").get_json()["records"]

    assert entry["message"] == sentence
    assert entry["outcome"] == "pass" and entry["stage"] == "Evaluate / clarity"
    assert entry["iteration"] == 5
    assert '"evaluated_in_iteration": 3' in entry["raw"]


# ---- tokens on the web pages ----------------------------------------------

_RUN_KEYS = {"input", "cache_read", "cache_write", "output", "total"}
_ROLLUP_KEYS = {"input", "cache_read", "cache_write", "output", "total", "not_reported"}


def _assert_no_hidden_token_fields(body):
    """reasoning, by_model, model names and cost never reach any JSON."""
    for hidden in ("reasoning", "by_model", "claude-opus", "cost", "usd"):
        assert hidden not in body.lower(), hidden


def test_loop_api_carries_tokens_per_step_iteration_and_loop(client, api_server, db, seed_run):
    """Every step has tokens; every iteration and the loop have tokens_total, in
    the briefing's keys, summed over agent_runs. A gate retry's two runs add up
    on their step; a carried-over pass and a pending step ran no agent: 0."""
    from minimise.interfaces.formatting import tokens_total
    from minimise.models import JobStatus, LoopStep, TaskStatus, TokenUsage
    from minimise.orchestration import loop_journal

    loop = _make_select_failed_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(100))
    jpath = api_server.loop_store.journal_path(loop.loop_id)

    def step(step_id, iteration, step_type, start, end, dimension=None, runs=(),
             status=TaskStatus.COMPLETED):
        db.create_loop_step(LoopStep(
            step_id=step_id, loop_id=loop.loop_id, iteration=iteration,
            step_type=step_type, dimension=dimension, status=status,
            started_at=_at(start) if start is not None else None,
            completed_at=_at(end) if end is not None else None,
        ))
        for tokens in runs:
            seed_run(db, tokens, loop_id=loop.loop_id, step_id=step_id)

    step("plan-1", 1, "plan", 0, 5, runs=[TokenUsage(
        10, 1000, 200, 50, reasoning=9, by_model={"claude-opus-5-5": {"input": 10}})])
    step("eval-1c", 1, "evaluate", 10, 20, "clarity", runs=[TokenUsage(1, 2, 3, 4)])
    step("eval-1a", 1, "evaluate", 10, 25, "accuracy", runs=[None])  # reported nothing
    step("eval-2a", 2, "evaluate", 30, 45, "accuracy",
         runs=[TokenUsage(3, None, None, 2), TokenUsage(2, None, None, 3)])  # a gate retry
    step("eval-2c", 2, "evaluate", 45, 45, "clarity")  # carried over: no agent ran
    step("impl-2", 2, "implement", None, None, status=TaskStatus.PENDING)
    loop_journal.append(jpath, {"step_type": "evaluate", "step_id": "eval-2c", "iteration": 2,
                                "dimension": "clarity", "verdict": "pass",
                                "evaluated_in_iteration": 1})

    response = client.get(f"/api/loops/{loop.loop_id}")
    data = response.get_json()

    served = data["steps"] + [s for g in data["timeline"]["groups"] for s in g["steps"]]
    assert all(s["tokens"] is None or set(s["tokens"]) == _RUN_KEYS for s in served)
    zeros = dict.fromkeys(_RUN_KEYS, 0)
    for steps in (data["steps"], [s for g in data["timeline"]["groups"] for s in g["steps"]]):
        steps = {s["step_id"]: s for s in steps}
        assert steps["plan-1"]["tokens"] == {"input": 10, "cache_read": 1000, "cache_write": 200,
                                             "output": 50, "total": 1260}
        assert steps["eval-2a"]["tokens"] == {"input": 5, "cache_read": None,
                                              "cache_write": None, "output": 5, "total": 10}
        assert steps["eval-1a"]["tokens"] is None
        assert steps["eval-2c"]["tokens"] == steps["impl-2"]["tokens"] == zeros
    timeline_carried = next(s for g in data["timeline"]["groups"] for s in g["steps"]
                            if s["step_id"] == "eval-2c")
    assert timeline_carried["carried_from"] == 1

    first, second = data["timeline"]["groups"]
    assert set(first["tokens_total"]) == set(second["tokens_total"]) == _ROLLUP_KEYS
    assert first["tokens_total"] == {"input": 11, "cache_read": 1002, "cache_write": 203,
                                     "output": 54, "total": 1270, "not_reported": 1}
    assert second["tokens_total"] == {"input": 5, "cache_read": None, "cache_write": None,
                                      "output": 5, "total": 10, "not_reported": 0}
    assert data["tokens_total"] == {"input": 16, "cache_read": 1002, "cache_write": 203,
                                    "output": 59, "total": 1280, "not_reported": 1}
    # the same rollup `mini loop status --format json` prints
    assert data["tokens_total"] == tokens_total(api_server.loop_store.load(loop.loop_id),
                                                db.list_agent_runs_for_loop(loop.loop_id))
    _assert_no_hidden_token_fields(response.get_data(as_text=True))


def test_loop_api_tokens_total_is_0_with_no_runs_and_null_when_none_reported(
        client, api_server, db, seed_run):
    from minimise.models import JobStatus, LoopStep, TaskStatus

    loop = _make_loop(api_server)
    zero = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "total": 0,
            "not_reported": 0}
    assert client.get(f"/api/loops/{loop.loop_id}").get_json()["tokens_total"] == zero

    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(8))
    db.create_loop_step(LoopStep(
        step_id="step-plan", loop_id=loop.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.COMPLETED, started_at=_at(0), completed_at=_at(2),
    ))
    seed_run(db, None, loop_id=loop.loop_id, step_id="step-plan")

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    nothing = {"input": None, "cache_read": None, "cache_write": None, "output": None,
               "total": None, "not_reported": 1}
    assert data["steps"][0]["tokens"] is None
    assert data["tokens_total"] == nothing
    assert data["timeline"]["groups"][0]["tokens_total"] == nothing


@pytest.mark.parametrize("status, not_reported", [("stopped", 1), ("running", 0)])
def test_loop_api_counts_an_unended_run_only_once_the_loop_stopped(
        client, api_server, db, seed_run, status, not_reported):
    import os
    from minimise.models import JobStatus, LoopStep, TaskStatus

    loop = _make_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus(status), started_at=_at(0),
                          pid=os.getpid())  # a live engine, so a RUNNING loop stays running
    db.create_loop_step(LoopStep(
        step_id="step-plan", loop_id=loop.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.RUNNING, started_at=_at(0),
    ))
    seed_run(db, ended=False, loop_id=loop.loop_id, step_id="step-plan")

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    assert data["tokens_total"]["not_reported"] == not_reported
    assert data["timeline"]["groups"][0]["tokens_total"] == data["tokens_total"]
    assert data["steps"][0]["tokens"] is None  # its run hasn't ended


def test_loop_api_untracked_loop_has_no_token_figures(client, api_server, db, seed_run, untrack):
    from minimise.models import JobStatus, LoopStep, TaskStatus, TokenUsage

    loop = _make_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(8))
    for step_id, step_type in (("step-plan", "plan"), ("step-impl", "implement")):
        db.create_loop_step(LoopStep(
            step_id=step_id, loop_id=loop.loop_id, iteration=1, step_type=step_type,
            status=TaskStatus.COMPLETED, started_at=_at(0), completed_at=_at(2),
        ))
    seed_run(db, TokenUsage(1, 2, 3, 4), loop_id=loop.loop_id, step_id="step-plan")
    untrack(db, loop_id=loop.loop_id)  # step-impl has no run: 0 if it were tracked

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    assert data["tokens_total"] is None
    assert [s["tokens"] for s in data["steps"]] == [None, None]
    [group] = data["timeline"]["groups"]
    assert group["tokens_total"] is None
    assert [s["tokens"] for s in group["steps"]] == [None, None]


_PRICES_YAML = """version: 0.0.1
prices:
  as_of: 2026-10-26
  models:
    claude-sonnet-5-5: {input: 3, cache_read: 0.3, cache_write: 6, output: 15}
    default: {input: 1, cache_read: 0.1, cache_write: 2, output: 5}
"""


def _priced_client(db, mock_job_controller, config_dir, settings=_PRICES_YAML):
    """A test client whose server reads settings.yaml from config_dir."""
    config_dir.mkdir(exist_ok=True)
    (config_dir / "settings.yaml").write_text(settings)
    server = APIServer(db, mock_job_controller, port=5002, config_dir=config_dir)
    server.app.testing = True
    return server.app.test_client()


def _cost_loop(api_server, db, seed_run, untracked=None):
    """Iteration 1: plan on sonnet and haiku (by_model, haiku at default), a
    clarity evaluator with no by_model (default), an accuracy evaluator that
    reported nothing. Iteration 2: a carried-over pass and a pending step."""
    from minimise.models import JobStatus, LoopStep, TaskStatus, TokenUsage

    loop = _make_select_failed_loop(api_server)
    db.update_loop_status(loop.loop_id, status=JobStatus.COMPLETED,
                          started_at=_at(0), completed_at=_at(100))
    by_model = {"claude-sonnet-5-5": {"input": 1_000_000, "cache_read": 2_000_000,
                                      "cache_write": 0, "output": 100_000},
                "claude-haiku-5-5": {"input": 0, "cache_read": 0, "cache_write": 0,
                                     "output": 1_000_000}}
    for step_id, iteration, step_type, dimension, runs, status in (
        ("plan-1", 1, "plan", None,
         [TokenUsage(1_000_000, 2_000_000, 0, 1_100_000, by_model=by_model)],
         TaskStatus.COMPLETED),
        ("eval-1c", 1, "evaluate", "clarity", [TokenUsage(0, 0, 1_000_000, 0)],
         TaskStatus.COMPLETED),
        ("eval-1a", 1, "evaluate", "accuracy", [None], TaskStatus.COMPLETED),
        ("eval-2c", 2, "evaluate", "clarity", [], TaskStatus.COMPLETED),
        ("impl-2", 2, "implement", None, [], TaskStatus.PENDING),
    ):
        done = status == TaskStatus.COMPLETED
        db.create_loop_step(LoopStep(
            step_id=step_id, loop_id=loop.loop_id, iteration=iteration, step_type=step_type,
            dimension=dimension, status=status, started_at=_at(0) if done else None,
            completed_at=_at(5) if done else None,
        ))
        for tokens in runs:
            seed_run(db, tokens, loop_id=loop.loop_id, step_id=step_id)
    return loop


def _dicts(value):
    """Every dict in a JSON value, nested ones included."""
    if isinstance(value, dict):
        yield value
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            yield from _dicts(item)


def test_loop_api_carries_cost_next_to_every_token_figure_when_priced(
        api_server, mock_job_controller, db, seed_run, temp_db_dir):
    loop = _cost_loop(api_server, db, seed_run)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")

    response = client.get(f"/api/loops/{loop.loop_id}")
    data = response.get_json()

    plan = (3 + 0.6 + 1.5) + 5  # sonnet's buckets; haiku's 1M output at default
    clarity = 2  # 1M cache write at default
    assert data["prices_as_of"] == "2026-10-26"
    timeline_steps = [s for g in data["timeline"]["groups"] for s in g["steps"]]
    for steps in (data["steps"], timeline_steps):
        assert {s["step_id"]: s["cost_usd"] for s in steps} == {
            "plan-1": pytest.approx(plan), "eval-1c": pytest.approx(clarity),
            "eval-1a": None,  # reported nothing: its tokens are null
            "eval-2c": 0, "impl-2": 0,  # no agent ran
        }
    first, second = data["timeline"]["groups"]
    assert first["cost_total"] == {"usd": pytest.approx(plan + clarity), "lower_bound": True}
    assert second["cost_total"] == {"usd": 0, "lower_bound": False}
    assert data["cost_total"] == first["cost_total"]
    for d in _dicts(data):
        assert ("tokens" in d) == ("cost_usd" in d)
        assert ("tokens_total" in d) == ("cost_total" in d)
    for hidden in ("reasoning", "by_model", "claude-sonnet", "claude-haiku"):
        assert hidden not in response.get_data(as_text=True), hidden


def test_loop_api_without_prices_is_unchanged(client, api_server, mock_job_controller, db,
                                              seed_run, temp_db_dir):
    loop = _cost_loop(api_server, db, seed_run)
    unpriced = _priced_client(db, mock_job_controller, temp_db_dir / "config",
                              "version: 0.0.1\nharness: claude\n")

    body = unpriced.get(f"/api/loops/{loop.loop_id}").get_data(as_text=True)

    assert body == client.get(f"/api/loops/{loop.loop_id}").get_data(as_text=True)
    for key in ("cost", "usd", "prices_as_of", "lower_bound"):
        assert key not in body


def test_loop_api_untracked_loop_has_null_cost(api_server, mock_job_controller, db, seed_run,
                                               untrack, temp_db_dir):
    loop = _cost_loop(api_server, db, seed_run)
    untrack(db, loop_id=loop.loop_id)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")

    data = client.get(f"/api/loops/{loop.loop_id}").get_json()

    assert data["cost_total"] is None
    assert [g["cost_total"] for g in data["timeline"]["groups"]] == [None, None]
    timeline_steps = [s for g in data["timeline"]["groups"] for s in g["steps"]]
    assert {s["cost_usd"] for s in data["steps"] + timeline_steps} == {None}


def test_loop_api_reads_prices_on_every_request(api_server, mock_job_controller, db, seed_run,
                                                temp_db_dir):
    loop = _cost_loop(api_server, db, seed_run)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")
    settings = temp_db_dir / "config" / "settings.yaml"
    url = f"/api/loops/{loop.loop_id}"

    assert client.get(url).get_json()["cost_total"]["usd"] == pytest.approx(12.1)
    settings.write_text(_PRICES_YAML.replace("cache_write: 2,", "cache_write: 20,"))
    assert client.get(url).get_json()["cost_total"]["usd"] == pytest.approx(30.1)
    settings.write_text("version: 0.0.1\n")
    assert "cost" not in client.get(url).get_data(as_text=True)


def test_loop_api_bad_prices_fail_naming_the_key(api_server, mock_job_controller, db,
                                                 seed_run, temp_db_dir):
    loop = _cost_loop(api_server, db, seed_run)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config",
                            _PRICES_YAML.replace("output: 15", "output: -15"))

    response = client.get(f"/api/loops/{loop.loop_id}")

    assert response.status_code == 500
    assert "prices.models.claude-sonnet-5-5.output" in response.get_json()["error"]


def _listed_loops(api_server, db, seed_run, untrack):
    """_cost_loop's loop, an untracked loop with a run, and a loop no agent ran in."""
    from minimise.models import LoopStep, TaskStatus, TokenUsage

    tracked = _cost_loop(api_server, db, seed_run)
    untracked = _make_loop(api_server)
    db.create_loop_step(LoopStep(
        step_id="old-plan", loop_id=untracked.loop_id, iteration=1, step_type="plan",
        status=TaskStatus.COMPLETED, started_at=_at(0), completed_at=_at(2),
    ))
    seed_run(db, TokenUsage(1, 2, 3, 4), loop_id=untracked.loop_id, step_id="old-plan")
    untrack(db, loop_id=untracked.loop_id)
    idle = _make_loop(api_server)
    return tracked, untracked, idle


def test_loops_list_api_carries_each_loops_tokens_total(client, api_server, mock_job_controller,
                                                        db, seed_run, untrack, temp_db_dir):
    tracked, untracked, idle = _listed_loops(api_server, db, seed_run, untrack)

    response = client.get("/api/loops")

    listed = {loop["loop_id"]: loop for loop in response.get_json()}
    assert listed[tracked.loop_id]["tokens_total"] == {
        "input": 1_000_000, "cache_read": 2_000_000, "cache_write": 1_000_000,
        "output": 1_100_000, "total": 5_100_000, "not_reported": 1,
    }
    assert listed[tracked.loop_id]["tokens_total"] == (
        client.get(f"/api/loops/{tracked.loop_id}").get_json()["tokens_total"])
    assert listed[untracked.loop_id]["tokens_total"] is None
    assert listed[idle.loop_id]["tokens_total"] == {
        "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "total": 0,
        "not_reported": 0,
    }
    # no prices: no cost, and a settings.yaml without prices changes nothing
    body = response.get_data(as_text=True)
    for key in ("cost", "usd", "lower_bound", "prices_as_of"):
        assert key not in body, key
    assert "X-Prices-As-Of" not in response.headers
    unpriced = _priced_client(db, mock_job_controller, temp_db_dir / "config",
                              "version: 0.0.1\n").get("/api/loops")
    assert unpriced.get_data(as_text=True) == body
    assert "X-Prices-As-Of" not in unpriced.headers


def test_loops_list_api_carries_cost_total_and_prices_header_when_priced(
        api_server, mock_job_controller, db, seed_run, untrack, temp_db_dir):
    tracked, untracked, idle = _listed_loops(api_server, db, seed_run, untrack)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")

    response = client.get("/api/loops")

    assert response.headers["X-Prices-As-Of"] == "2026-10-26"
    assert response.headers["X-Has-Next"] == "false"
    listed = {loop["loop_id"]: loop for loop in response.get_json()}
    plan, clarity = (3 + 0.6 + 1.5) + 5, 2  # as in the detail API's cost test
    assert listed[tracked.loop_id]["cost_total"] == {
        "usd": pytest.approx(plan + clarity), "lower_bound": True}  # accuracy reported nothing
    assert listed[tracked.loop_id]["cost_total"] == (
        client.get(f"/api/loops/{tracked.loop_id}").get_json()["cost_total"])
    assert listed[untracked.loop_id]["cost_total"] is None
    assert listed[idle.loop_id]["cost_total"] == {"usd": 0, "lower_bound": False}
    assert all("tokens_total" in loop for loop in listed.values())
    assert not any("prices_as_of" in loop for loop in listed.values())  # it's the header


def test_loops_list_loads_the_pages_runs_in_one_query(api_server, mock_job_controller, db,
                                                      seed_run, untrack, temp_db_dir,
                                                      monkeypatch):
    loops = _listed_loops(api_server, db, seed_run, untrack)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")
    monkeypatch.setattr(db, "list_agent_runs_for_loop",
                        Mock(side_effect=AssertionError("one runs query per loop")))
    queries, query = [], db._query

    def counting(sql, params=()):
        if "agent_runs" in sql:
            queries.append(params)
        return query(sql, params)

    monkeypatch.setattr(db, "_query", counting)

    for url in ("/api/loops", "/loops"):
        queries.clear()
        assert client.get(url).status_code == 200, url
        assert [sorted(params) for params in queries] == [
            sorted(loop.loop_id for loop in loops)], url


def test_loops_list_api_bad_prices_fail_naming_the_key(api_server, mock_job_controller, db,
                                                       seed_run, temp_db_dir):
    _cost_loop(api_server, db, seed_run)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config",
                            _PRICES_YAML.replace("input: 1,", "input: cheap,"))

    response = client.get("/api/loops")

    assert response.status_code == 500
    assert "prices.models.default.input" in response.get_json()["error"]


def test_loops_list_page_shows_each_loops_tokens(client, api_server, db, seed_run, untrack):
    import re

    tracked, untracked, idle = _listed_loops(api_server, db, seed_run, untrack)

    html = client.get("/loops").get_data(as_text=True)

    assert re.findall(r"<th>(\w+)</th>", html) == [
        "ID", "Name", "Status", "Iteration", "Stage", "Tokens", "Created"]

    def tokens_cell(loop):
        row = html[html.index(f">{loop.loop_id}</a>"):]
        return re.search(r'<td data-label="Tokens">(.*?)</td>', row).group(1)

    # the same HTML loop.js writes with figures on every refresh (unpriced: tokensFigure's)
    assert tokens_cell(tracked) == (
        '<span class="tokens" title="input 1.0M · cache write 1.0M · cache read 2.0M · '
        'output 1.1M (1 run not reported)">5.1M</span>')
    assert tokens_cell(untracked) == "—"
    assert tokens_cell(idle) == "0"
    js = client.get("/static/loop.js").get_data(as_text=True)
    assert '<td data-label="Tokens">${figures(loop.tokens_total, loop.cost_total)}</td>' in js
    assert '<td colspan="7" class="empty">No loops yet</td>' in js


def test_loops_list_page_empty_state_spans_the_tokens_column(client):
    html = client.get("/loops").get_data(as_text=True)

    assert '<td colspan="7" class="empty">No loops yet</td>' in html


def test_loop_detail_page_shows_tokens_next_to_elapsed_and_duration(client, api_server):
    import re

    loop = _make_loop(api_server)

    html = client.get(f"/loops/{loop.loop_id}/view").get_data(as_text=True)

    summary = html[html.index('class="live-summary"'):html.index('<div class="table-wrap">')]
    tiles = re.findall(r"<span>([^<]+)</span>", summary)
    assert tiles.index("Tokens") == tiles.index("Elapsed") + 1
    assert '<strong id="loop-tokens">—</strong>' in summary
    assert '<small id="loop-tokens-note"></small>' in summary
    table = html[html.index('<table class="timeline-table">'):html.index("</table>")]
    assert '<col class="col-duration"><col class="col-tokens">' in table

    js = client.get("/static/loop.js").get_data(as_text=True)
    assert 'setHtml("loop-tokens", figures(loop.tokens_total, loop.cost_total))' in js
    assert 'setText("loop-tokens-note", tokensNotReported(loop.tokens_total))' in js
    assert 'data-label="Tokens">${figures(g.tokens_total, g.cost_total)}</td>' in js
    # a carried-over pass shows its 0 like any other row (no agent ran in it)
    assert 'data-label="Tokens">${figures(s.tokens, s.cost_usd)}</td>' in js
    assert "carried ? \"\"" not in js
    assert 'colspan="5" class="empty">No loop steps yet' in js


def test_job_detail_page_shows_task_agent_tokens(client, mock_job_controller):
    import re

    job = _make_job(mock_job_controller)

    html = client.get(f"/jobs/{job.id}/view").get_data(as_text=True)

    summary = html[html.index('class="live-summary"'):html.index('<div class="table-wrap">')]
    tiles = re.findall(r"<span>([^<]+)</span>", summary)
    assert tiles.index("Tokens") == tiles.index("Elapsed") + 1
    assert '<strong id="job-tokens">—</strong>' in summary
    assert '<small id="job-tokens-note">task agents only</small>' in summary
    table = html[html.index('<table class="timeline-table">'):html.index("</table>")]
    assert re.findall(r"<th>(\w+)", table) == [
        "Step", "Status", "Duration", "Estimate", "Timeout", "Tokens", "Timeline",
    ]
    assert '<td colspan="7" class="empty">Loading timeline…</td>' in table

    js = client.get("/static/poll.js").get_data(as_text=True)
    # the job's total is top-level in GET /jobs/<id>, not in its timeline
    assert 'setHtml("job-tokens", figures(job.tokens_total, job.cost_total))' in js
    assert '["task agents only", tokensNotReported(job.tokens_total)]' in js
    assert "tl.tokens_total" not in js
    assert 'data-label="Tokens">${figures(g.tokens_total, g.cost_total)}</td>' in js
    assert 'data-label="Tokens">${figures(s.tokens, s.cost_usd)}</td>' in js  # null on hooks: "—"
    assert 'colspan="7" class="empty">No tasks' in js


def test_web_token_figures_match_the_clis_format(client, tmp_path):
    """poll.js's formatTokens prints what format_tokens prints, halves included
    (Python rounds 1250 to 1.2K; plain toFixed would say 1.3K), and its
    tokensFigure what the server's _tokens_figure renders."""
    import json
    import shutil
    import subprocess
    from minimise.interfaces.api_server import _tokens_figure
    from minimise.interfaces.formatting import format_tokens

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = tmp_path / "poll.js"
    script.write_text(client.get("/static/poll.js").get_data(as_text=True))
    values = [None, 0, 7, 999, 1000, 1049, 1050, 1150, 1250, 1750, 12_449, 99_950, 999_949,
              999_950, 999_999, 1_000_000, 1_250_000, 1_750_000, 243_149_999, 243_150_000,
              *range(0, 20_000, 7), *range(0, 50_000_000, 250_000)]
    figures = [
        None,
        {"input": 1200, "cache_read": 2_100_000, "cache_write": 3, "output": 40,
         "total": 2_101_243},
        {"input": None, "cache_read": None, "cache_write": None, "output": None,
         "total": None, "not_reported": 2},
        {"input": 5, "cache_read": None, "cache_write": None, "output": 5,
         "total": 10, "not_reported": 1},
        {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "total": 0},
        {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "total": 0,
         "not_reported": 0},
    ]
    runner = (
        "const vm = require('vm'), fs = require('fs'); const ctx = {};"
        "vm.createContext(ctx); vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);"
        "const [values, figures] = JSON.parse(fs.readFileSync(0, 'utf8'));"
        "console.log(JSON.stringify([values.map(ctx.formatTokens),"
        " figures.map(f => ctx.tokensFigure(f))]));"
    )
    out = subprocess.run(["node", "-e", runner, str(script)], input=json.dumps([values, figures]),
                         capture_output=True, text=True, check=True, timeout=30).stdout

    formatted, shown = json.loads(out)
    assert formatted == [format_tokens(v) for v in values]
    assert shown == [
        "—",
        '<span class="tokens" title="input 1.2K · cache write 3 · cache read 2.1M · output 40">'
        "2.1M</span>",
        '<span class="tokens" title="(2 runs not reported)">—</span>',
        '<span class="tokens" title="input 5 · cache write — · cache read — · output 5 '
        '(1 run not reported)">10</span>',
        "0",  # a row no agent ran in, e.g. a carried-over pass
        "0",  # a rollup with no counted runs
    ]
    # the list pages' server-rendered cells are the HTML their polling writes
    assert [str(_tokens_figure(f)) for f in figures] == shown


# ---- the Tokens/cost toggle: only on priced pages ---------------------------

_COST_NOTE = ('<p class="cost-note fig-cost">Estimated at your prices as of '
              '<span class="prices-as-of">2026-10-26</span>, to build intuition, '
              'not to track what you actually pay.</p>')
_TOGGLE_HEADER = (
    '<th><button type="button" class="figure-toggle" data-figure-toggle aria-pressed="false" '
    'title="Switch between tokens and estimated cost"><span class="fig-tokens">Tokens</span>'
    '<span class="fig-cost">Cost</span></button></th>')
_TOGGLE_TILE = (
    '<div data-figure-toggle role="button" tabindex="0" aria-pressed="false" '
    'title="Switch between tokens and estimated cost">\n{indent}<span><span class="fig-tokens">'
    'Tokens</span><span class="fig-cost">Est. cost</span></span>\n{indent}<strong id="{id}">')


def _toggle_pages(api_server, mock_job_controller):
    """The four pages with token figures: url -> its Tokens tile (detail pages)."""
    job = _make_job(mock_job_controller)
    loop = _make_loop(api_server)
    return {
        "/": None,
        "/loops": None,
        f"/jobs/{job.id}/view": _TOGGLE_TILE.format(indent=" " * 12, id="job-tokens"),
        f"/loops/{loop.loop_id}/view": _TOGGLE_TILE.format(indent=" " * 16, id="loop-tokens"),
    }


def test_priced_pages_render_the_tokens_cost_toggle_and_note(api_server, mock_job_controller,
                                                              db, temp_db_dir):
    import re

    pages = _toggle_pages(api_server, mock_job_controller)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")

    for url, tile in pages.items():
        html = client.get(url).get_data(as_text=True)

        assert html.count(_TOGGLE_HEADER) == 1, url
        if tile:
            assert html.count(tile) == 1, url
        assert html.count("data-figure-toggle") == (2 if tile else 1), url
        assert html.count(_COST_NOTE) == 1, url  # one note per page, near the table
        table = html.index('<table class="timeline-table">' if tile else "<table>")
        assert html.index(_COST_NOTE) < table, url
        if tile:
            assert html.index('class="live-summary"') < html.index(_COST_NOTE), url
        assert re.sub("<[^>]+>", "", _COST_NOTE) == (
            "Estimated at your prices as of 2026-10-26, to build intuition, "
            "not to track what you actually pay.")
        assert 'initFigureToggle("2026-10-26");' in html, url


def test_unpriced_pages_have_no_toggle_and_no_note(client, api_server, mock_job_controller, db,
                                                   temp_db_dir):
    pages = _toggle_pages(api_server, mock_job_controller)
    unpriced = _priced_client(db, mock_job_controller, temp_db_dir / "config",
                              "version: 0.0.1\nharness: claude\n")

    for url, tile in pages.items():
        html = client.get(url).get_data(as_text=True)

        for hook in ("data-figure-toggle", "figure-toggle", "fig-cost", "fig-tokens",
                     "Est. cost", "Estimated at your prices", "prices-as-of"):
            assert hook not in html, (url, hook)
        assert "<th>Tokens</th>" in html, url
        if tile:
            assert "<div>\n" + " " * (16 if "loops" in url else 12) + "<span>Tokens</span>" in html
        assert "initFigureToggle(null);" in html, url
        # a settings.yaml without prices renders the same page
        assert unpriced.get(url).get_data(as_text=True) == html, url


def test_pages_with_bad_prices_still_render_tokens_only(api_server, mock_job_controller, db,
                                                        temp_db_dir):
    pages = _toggle_pages(api_server, mock_job_controller)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config",
                            _PRICES_YAML.replace("output: 15", "output: -15"))

    for url in pages:
        response = client.get(url)

        assert response.status_code == 200, url
        html = response.get_data(as_text=True)
        assert "data-figure-toggle" not in html and "Estimated at" not in html, url
        assert "initFigureToggle(null);" in html, url


def test_priced_loops_list_rows_carry_tokens_and_cost(api_server, mock_job_controller, db,
                                                      seed_run, untrack, temp_db_dir):
    import re

    tracked, untracked, idle = _listed_loops(api_server, db, seed_run, untrack)
    client = _priced_client(db, mock_job_controller, temp_db_dir / "config")

    html = client.get("/loops").get_data(as_text=True)

    def tokens_cell(loop):
        row = html[html.index(f">{loop.loop_id}</a>"):]
        return re.search(r'<td data-label="Tokens">(.*?)</td>', row).group(1)

    # the HTML loop.js's figures writes: both figures; the toggle shows one
    assert tokens_cell(tracked) == (
        '<span class="fig-tokens"><span class="tokens" title="input 1.0M · cache write 1.0M · '
        'cache read 2.0M · output 1.1M (1 run not reported)">5.1M</span></span>'
        '<span class="fig-cost">≥ $12.10</span>')  # accuracy reported nothing: a lower bound
    assert tokens_cell(untracked) == '<span class="fig-tokens">—</span><span class="fig-cost">—</span>'
    assert tokens_cell(idle) == '<span class="fig-tokens">0</span><span class="fig-cost">$0</span>'


def test_polling_keeps_the_toggles_prices_as_of_current(client):
    poll = client.get("/static/poll.js").get_data(as_text=True)
    loop = client.get("/static/loop.js").get_data(as_text=True)

    # lists read the header, detail pages the JSON key
    assert 'setPricesAsOf(resp.headers.get("X-Prices-As-Of"));' in poll
    assert "setPricesAsOf(job.prices_as_of);" in poll
    assert 'setPricesAsOf(response.headers.get("X-Prices-As-Of"));' in loop
    assert "setPricesAsOf(loop.prices_as_of);" in loop
    # every figure cell goes through figures(): tokensFigure's only callers are in it
    assert poll.count("tokensFigure(") == 3  # its definition, and figures' two calls
    assert "tokensFigure(" not in loop


def _run_poll_js(client, tmp_path, runner, payload):
    import json
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = tmp_path / "poll.js"
    script.write_text(client.get("/static/poll.js").get_data(as_text=True))
    out = subprocess.run(["node", "-e", runner, str(script)], input=json.dumps(payload),
                         capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


def test_web_cost_figures_match_format_cost(client, tmp_path):
    """poll.js's formatCost prints exactly what format_cost prints, halves and
    the $100 switch included, and costFigure / figures what the templates'
    filters render."""
    import random
    from minimise.interfaces.api_server import _cost_figure, _figures
    from minimise.interfaces.formatting import format_cost

    rng = random.Random(20261026)
    values = [None, 0, 0.0, 1e-9, 0.001, 0.004999, 0.005, 0.00999999, 0.01, 0.0100001,
              0.015, 0.125, 0.375, 0.625, 1.005, 1.125, 2.675, 0.94, 0.945, 9.995, 99.99,
              99.994, 99.995, 99.996, 99.999, 100, 100.5, 101.5, 999.5, 1234.4, 1234.5,
              99_999.5, 1_000_000.49, 12_345_678.5, 3, 15, 0.3,
              *(i / 8 for i in range(0, 2000)), *(i / 2 for i in range(150, 2500)),
              *(i * 0.005 for i in range(0, 3000)), *(i / 1000 for i in range(0, 20_000, 7)),
              *(10 ** rng.uniform(-4, 8) for _ in range(3000))]
    cases = [[v, lower] for v in values for lower in (False, True)]
    costs = [None, 0, 0.004, 0.94, 1234.5, {"usd": None, "lower_bound": True},
             {"usd": 0, "lower_bound": False}, {"usd": 0.004, "lower_bound": True},
             {"usd": 12.1, "lower_bound": True}, {"usd": 99.996, "lower_bound": False}]
    tokens = {"input": 1200, "cache_read": 2_100_000, "cache_write": 3, "output": 40,
              "total": 2_101_243}
    pairs = [[None], [tokens], *([t, c] for t in (None, tokens) for c in costs)]
    runner = (
        "const vm = require('vm'), fs = require('fs'); const ctx = {};"
        "vm.createContext(ctx); vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);"
        "const [cases, costs, pairs] = JSON.parse(fs.readFileSync(0, 'utf8'));"
        "console.log(JSON.stringify([cases.map(([v, lower]) => ctx.formatCost(v, lower)),"
        " costs.map(c => ctx.costFigure(c)), pairs.map(([t, ...c]) => ctx.figures(t, ...c))]));"
    )

    formatted, cost_cells, figure_cells = _run_poll_js(client, tmp_path, runner,
                                                       [cases, costs, pairs])

    assert formatted == [format_cost(v, lower) for v, lower in cases]
    assert {"$0", "<$0.01", "$0.13", "$1.13", "$100", "$101", "$1,234", "$1,235",
            "$12,345,679", "≥ $0.94", "—"} <= set(formatted)  # the rules are all covered
    assert cost_cells == [str(_cost_figure(c)) for c in costs]
    assert cost_cells[2] == "&lt;$0.01" and cost_cells[7] == "≥ &lt;$0.01"
    assert figure_cells == [str(_figures(*pair)) for pair in pairs]
    assert figure_cells[0] == "—"  # no prices: the tokens alone, as before


def test_figure_toggle_flips_every_page_and_remembers_the_choice(client, tmp_path):
    """initFigureToggle against a stub DOM: cost by default; a click flips
    the root to tokens and stores it, so the next page opens on tokens; a second
    click flips back; a broken localStorage still flips; no prices, no toggle."""
    runner = r"""
const vm = require('vm'), fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
function page(asOf, {storage, toggles = 2}) {
    const els = Array.from({length: toggles}, (_, i) => ({
        tagName: i ? 'DIV' : 'BUTTON', attrs: {}, listeners: {},
        addEventListener(type, fn) { this.listeners[type] = fn; },
        setAttribute(name, value) { this.attrs[name] = value; },
    }));
    const note = {textContent: 'stale'};
    const root = {dataset: {}};
    const document = {documentElement: root, querySelectorAll: sel =>
        sel === '[data-figure-toggle]' ? els : sel === '.prices-as-of' ? [note] : []};
    const ctx = {document, localStorage: storage};
    vm.createContext(ctx);
    vm.runInContext(src, ctx);
    ctx.initFigureToggle(asOf);
    const state = () => ({figures: root.dataset.figures ?? null, note: note.textContent,
                          pressed: els.map(el => el.attrs['aria-pressed'] ?? null)});
    return {ctx, els, state};
}
const store = {}, storage = {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; }};
const broken = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }};
const out = {};
let p = page('2026-10-26', {storage});
out.fresh = p.state();
p.els[0].listeners.click();
out.clicked = [p.state(), store.figures];
p = page('2026-10-26', {storage});  // another page: the choice carries over
out.nextPage = p.state();
let prevented = false;
p.els[1].listeners.keydown({key: 'Enter', preventDefault() { prevented = true; }});
out.tileKey = [p.state(), store.figures, prevented, 'keydown' in p.els[0].listeners];
p.els[1].listeners.click();
p.ctx.setPricesAsOf('2026-11-01');  // a poll brought a new as-of date
out.polled = p.state();
p.ctx.setPricesAsOf(null);  // prices removed while the page is open
out.unpriced = p.state();
p.els[0].listeners.click();
out.unpricedClick = [p.state(), store.figures];
p = page('2026-10-26', {storage: broken});
out.brokenFresh = p.state();
p.els[0].listeners.click();
out.brokenClick = p.state();
out.noToggles = page('2026-10-26', {storage, toggles: 0}).state();
out.noPrices = page(null, {storage, toggles: 0}).state();
console.log(JSON.stringify(out));
"""
    out = _run_poll_js(client, tmp_path, runner, None)

    asof = "2026-10-26"
    assert out["fresh"] == {"figures": "cost", "note": asof, "pressed": ["true", "true"]}
    assert out["clicked"] == [{"figures": "tokens", "note": asof, "pressed": ["false", "false"]},
                              "tokens"]
    assert out["nextPage"] == {"figures": "tokens", "note": asof, "pressed": ["false", "false"]}
    # the tile (not a button) flips on Enter; the header button gets its keys natively
    assert out["tileKey"] == [{"figures": "cost", "note": asof, "pressed": ["true", "true"]},
                              "cost", True, False]
    assert out["polled"] == {"figures": "tokens", "note": "2026-11-01",
                             "pressed": ["false", "false"]}
    assert out["unpriced"]["figures"] is None  # tokens show; the stored choice stays
    assert out["unpricedClick"] == [out["unpriced"], "tokens"]
    assert out["brokenFresh"]["figures"] == "cost"
    assert out["brokenClick"]["figures"] == "tokens"
    assert out["noToggles"]["figures"] is None
    assert out["noPrices"]["figures"] is None


def test_toggle_css_shows_one_figure_at_a_time(client):
    css = client.get("/static/style.css").get_data(as_text=True)

    assert '[data-figures="cost"] .fig-tokens,\n:root:not([data-figures="cost"]) .fig-cost ' \
           '{ display: none; }' in css
    detail = client.get("/static/detail.css").get_data(as_text=True)
    # a figure's spans inside a tile's strong aren't styled as the tile's label
    assert ".live-summary span" not in detail
    assert ".live-summary > div > span {" in detail
