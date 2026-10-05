"""REST API server exposing read-only job/task state over HTTP."""

import json
import threading
from collections import deque
from datetime import datetime
from typing import Optional

import markdown
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from minimise.interfaces.loop_views import register_loop_routes
from minimise.interfaces.timeline import build_steps, project_steps, steps_from_executions
from minimise.models import Job, JobStatus, Plan, TaskStatus
from minimise.storage.database import Database
from minimise.storage.loop_store import LoopStore
from minimise.orchestration.job_controller import JobController
from minimise.personas import load_personas

NAV_LINKS = [
    ("Jobs", "job_list_page"),
    ("Loops", "loop_list_page"),
    ("Personas", "personas_page"),
]
JOBS_PAGE_SIZE = 50


def _duration_label(minutes: Optional[int]) -> str:
    """Format a positive minute count for compact human-readable UI labels."""
    if minutes is None:
        return ""
    hours, remaining = divmod(minutes, 60)
    if not hours:
        return f"{remaining} min"
    if not remaining:
        return f"{hours} hr"
    return f"{hours} hr {remaining} min"


def _plan_summary(plan: Plan) -> dict:
    """Return the small set of derived metrics used by the detail page."""
    hooks = [*plan.pre_hooks, *plan.post_hooks]
    for task in plan.tasks:
        hooks.extend(task.pre_hooks)
        hooks.extend(task.post_hooks)
    task_minutes = sum(task.estimated_duration_min for task in plan.tasks)
    hook_minutes = sum(hook.estimated_duration_min for hook in hooks)
    return {
        "task_count": len(plan.tasks),
        "hook_count": len(hooks),
        "total_minutes": task_minutes + hook_minutes,
    }


TERMINAL_JOB_STATUSES = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.STOPPED)
PLAN_HOOK_GROUPS = {"pre_plan": "Before all tasks", "post_plan": "After all tasks"}
_TASK_PHASE_ORDER = {"pre_task": 0, "task": 1, "post_task": 2}


def _group_steps(steps: list) -> list:
    """Split step indices into groups: plan pre hooks, one group per task (its
    pre hooks, attempts and post hooks), then plan post hooks.

    Task steps join their task_id's group wherever they appear, so the
    execution-ordered fallback groups too. Steps without a task_id (the job's
    task rows are missing) start a new group where the plan order starts a new
    task. Returns (phase, indices) pairs; empty plan-hook groups are dropped.
    """
    before, after, tasks, by_task = [], [], [], {}
    prev = None
    for i, step in enumerate(steps):
        if step.phase in PLAN_HOOK_GROUPS:
            (before if step.phase == "pre_plan" else after).append(i)
        elif step.task_id is not None:
            if step.task_id not in by_task:
                by_task[step.task_id] = []
                tasks.append(by_task[step.task_id])
            by_task[step.task_id].append(i)
        else:
            same_task = (prev is not None and prev.task_id is None
                         and prev.phase in _TASK_PHASE_ORDER
                         and _TASK_PHASE_ORDER.get(step.phase, 1) >= _TASK_PHASE_ORDER[prev.phase]
                         and not step.phase == prev.phase == "task")
            if not same_task:
                tasks.append([])
            tasks[-1].append(i)
        prev = step
    return ([("pre_plan", before)] if before else []) + [("task", t) for t in tasks] \
        + ([("post_plan", after)] if after else [])


def _rollup_status(parts: list) -> str:
    statuses = {p["status"] for p in parts}
    for status in ("running", "failed", "stopped"):
        if status in statuses:
            return status
    return "completed" if statuses == {"completed"} else "pending"


def _timeline_group(kind: str, name: str, task_id: Optional[str], status: str,
                    parts: list) -> dict:
    """Aggregate row over a group's parts: wall time from the first start to the
    last end (parts can overlap or leave gaps, so never a sum), and estimates
    summed with the task's own estimate counted once however many attempts ran."""
    start = min((p["start_offset"] for p in parts if p["start_offset"] is not None),
                default=None)
    ends = [p["start_offset"] + p["duration"] for p in parts
            if p["start_offset"] is not None and p["duration"] is not None]
    task_estimate = next((p["estimate_secs"] for p in parts if p["kind"] == "task"), None)
    estimates = [p["estimate_secs"] for p in parts if p["kind"] == "hook"] + [task_estimate]
    return {
        "kind": kind,
        "name": name,
        "task_id": task_id,
        "status": status,
        "start_offset": start,
        # A running group has no fixed duration; the client ticks it from start.
        "duration": round(max(ends) - start, 1) if ends and status != "running" else None,
        "estimate_secs": sum(e for e in estimates if e) or None,
        "steps": parts,
    }


def _job_timeline(job: Job, steps: list, now: datetime) -> dict:
    """Group every step by task (and plan hooks by phase) on one timeline
    measured in seconds from its origin.

    started_at resets on every run, so a resumed job's earlier steps began
    before it: the origin is whichever came first, and ``run_start_offset``
    marks where the current run began. The client renders bars from these
    offsets and ticks running durations forward from ``now_offset``, so
    browser clock skew never matters. A job that hasn't started is laid out
    from zero by estimates alone.
    """
    origin = min([t for t in (job.started_at, *(s.started_at for s in steps)) if t],
                 default=now)
    finished = job.status in TERMINAL_JOB_STATUSES
    end = (job.completed_at or now) if finished else now
    placements, total = project_steps(steps, origin, end)

    def offset(moment: Optional[datetime]) -> Optional[float]:
        return round((moment - origin).total_seconds(), 1) if moment else None

    rows = []
    for step, (start, actual_end, projected_end) in zip(steps, placements):
        ran = step.status != TaskStatus.PENDING
        rows.append({
            "name": step.name,
            "phase": step.phase,
            "kind": "hook" if step.is_hook else "task",
            "task_id": step.task_id,
            "attempt": step.attempt,
            "status": step.status.value,
            "assignee": step.assignee,
            "exit_reason": step.exit_reason,
            "estimate_secs": step.estimate * 60 if step.estimate else None,
            "timeout_secs": step.timeout * 60 if step.timeout else None,
            "start_offset": offset(step.started_at),
            "duration": (
                round((step.ended_at - step.started_at).total_seconds(), 1)
                if step.started_at and step.ended_at else None
            ),
            # A finished job never runs its pending steps, so don't project them.
            "bar": None if finished and not ran else {
                "start": round(start, 1),
                "actual_end": round(actual_end, 1),
                "projected_end": round(projected_end, 1),
            },
        })
    if finished:
        total = max([r["bar"]["projected_end"] for r in rows if r["bar"]] + [offset(end) or 0, 1])

    tasks = {t.id: t for t in job.tasks}
    groups = []
    for phase, indices in _group_steps(steps):
        parts = [rows[i] for i in indices]
        if phase in PLAN_HOOK_GROUPS:
            groups.append(_timeline_group("plan_hooks", PLAN_HOOK_GROUPS[phase], None,
                                          _rollup_status(parts), parts))
            continue
        task_id = parts[0]["task_id"]
        task = tasks.get(task_id)
        name = task.name if task else next(
            (p["name"] for p in parts if p["kind"] == "task"), task_id or "")
        # pre_task hooks run while the task is still PENDING, so a running part wins
        status = "running" if any(p["status"] == "running" for p in parts) else (
            task.status.value if task else _rollup_status(parts))
        groups.append(_timeline_group("task", name, task_id, status, parts))

    return {
        "now_offset": offset(end) if job.started_at else None,
        "run_start_offset": offset(job.started_at),
        "total_secs": round(total, 1),
        "planned_secs": sum(g["estimate_secs"] or 0 for g in groups),
        "groups": groups,
    }


def _persona_summary(system_prompt: str, width: int = 70) -> str:
    """First non-empty line of the prompt, truncated to width (mirrors cli/persona.py)."""
    line = next((ln.strip() for ln in system_prompt.splitlines() if ln.strip()), "")
    return line if len(line) <= width else line[: width - 1] + "…"


class APIServer:
    """REST API server exposing read-only job/task state over HTTP."""

    def __init__(self, db: Database, job_controller: JobController, port: int = 5000):
        """
        Initialize the API server.

        Args:
            db: Database instance for accessing job/task data
            job_controller: JobController instance for job operations
            port: Port to run the server on (default: 5000)
        """
        self.db = db
        self.job_controller = job_controller
        self.loop_store = LoopStore(db, job_controller.store.jobs_dir)
        self.port = port
        self.app = Flask(__name__)
        self.app.add_template_filter(_duration_label, "duration_label")

        # Enable CORS
        CORS(self.app, resources={r"/*": {"origins": "*"}})

        self.server_thread: Optional[threading.Thread] = None

        # Register routes
        self._register_routes()
        register_loop_routes(self.app, self.db, self.loop_store)

    def _load_job_with_tasks(self, job_id: str) -> Optional[Job]:
        """Fetch a job and attach its task list, or None if it doesn't exist."""
        return self.job_controller.store.load(job_id)

    def _timeline_steps(self, job: Job, executions: list) -> list:
        """Plan-ordered steps, or execution order if the cached plan is unreadable."""
        try:
            plan = self.job_controller.store.load_plan(job.id)
        except Exception:
            return steps_from_executions(job.tasks, executions)
        return build_steps(plan, job.tasks, executions)

    def _load_page_with_tasks(self, page: int) -> tuple[list[Job], bool]:
        """List one page (1-indexed) of jobs with tasks attached; returns (jobs, has_next)."""
        offset = (page - 1) * JOBS_PAGE_SIZE
        jobs = self.job_controller.store.load_many(limit=JOBS_PAGE_SIZE + 1, offset=offset)
        has_next = len(jobs) > JOBS_PAGE_SIZE
        jobs = jobs[:JOBS_PAGE_SIZE]
        for job in jobs:
            job.tasks = self.db.list_tasks_for_job(job.id)
        return jobs, has_next

    def _register_routes(self):
        """Register all REST API routes."""

        @self.app.context_processor
        def inject_nav_links():
            return {"nav_links": NAV_LINKS}

        @self.app.route("/", methods=["GET"])
        def job_list_page():
            """Server-rendered job list page, polled client-side via /jobs."""
            page = max(1, request.args.get("page", 1, type=int))
            jobs, has_next = self._load_page_with_tasks(page)
            return render_template("list.html", jobs=jobs, page=page, has_next=has_next)

        @self.app.route("/jobs/<job_id>/view", methods=["GET"])
        def job_detail_page(job_id: str):
            job = self._load_job_with_tasks(job_id)
            if job is None:
                return "Job not found", 404

            plan = None
            plan_raw = None
            plan_error = None
            plan_summary = None
            plan_briefing = None
            plan_yaml_path = self.job_controller.store.jobs_dir / job_id / "plan.yaml"
            try:
                plan_raw = plan_yaml_path.read_text()
            except FileNotFoundError:
                plan_error = "The cached plan file is missing for this job."
            except OSError as e:
                plan_error = f"The cached plan file could not be read: {e}"
            else:
                try:
                    plan = self.job_controller.store.load_plan(job_id)
                    plan_summary = _plan_summary(plan)
                    plan_briefing = plan.briefing
                except Exception as e:
                    # A historical plan can become invalid after a schema change.
                    # Keep operational status and logs available in that case.
                    plan_error = f"The cached plan could not be parsed: {e}"

            return render_template(
                "detail.html",
                job=job,
                plan=plan,
                plan_raw=plan_raw,
                plan_error=plan_error,
                plan_summary=plan_summary,
                plan_briefing=plan_briefing,
            )

        @self.app.route("/personas", methods=["GET"])
        def personas_page():
            """Server-rendered persona list page, grouped BUILTIN vs USER."""
            import minimise.interfaces.cli as _cli  # lazy: avoids circular import (cli.view imports us)

            personas = load_personas(_cli.CONFIG_DIR)
            # Bare `latest` names only — skip the per-version @vN aliases.
            names = [n for n in personas if "@" not in n]
            groups = [
                ("BUILTIN", sorted(n for n in names if n.startswith("mini:"))),
                ("USER", sorted(n for n in names if not n.startswith("mini:"))),
            ]
            groups = [
                (tag, [(n, _persona_summary(personas[n].system_prompt)) for n in group])
                for tag, group in groups
            ]
            return render_template("personas.html", groups=groups)

        @self.app.route("/personas/<name>", methods=["GET"])
        def persona_detail_page(name: str):
            import minimise.interfaces.cli as _cli  # lazy: avoids circular import (cli.view imports us)

            personas = load_personas(_cli.CONFIG_DIR)
            p = personas.get(name)
            if p is None:
                return "Persona not found", 404
            prompt_html = markdown.markdown(p.system_prompt)
            return render_template("persona_detail.html", persona=p, prompt_html=prompt_html)

        @self.app.route("/jobs", methods=["GET"])
        def get_jobs():
            """Get one page of jobs, each with its task list attached."""
            try:
                page = max(1, request.args.get("page", 1, type=int))
                jobs, has_next = self._load_page_with_tasks(page)
                resp = jsonify([job.to_dict() for job in jobs])
                resp.headers["X-Has-Next"] = "true" if has_next else "false"
                return resp, 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs", methods=["POST"])
        def create_job():
            """Create a new job from a plan file."""
            try:
                data = request.get_json()
                if not data or "plan_path" not in data:
                    return jsonify({"error": "Missing plan_path in request body"}), 400

                plan_path = data["plan_path"]
                job = self.job_controller.create_job(plan_path)

                if job is None:
                    return jsonify({"error": "Failed to create job"}), 500

                return jsonify(job.to_dict()), 201
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>", methods=["GET"])
        def get_job(job_id: str):
            """Get job details with task list."""
            try:
                job = self._load_job_with_tasks(job_id)
                if job is None:
                    return jsonify({"error": "Job not found"}), 404

                executions = self.db.list_executions_for_job(job_id)
                hooks = [
                    {
                        "hook_name": ex.hook_name or "",
                        "execution_type": ex.execution_type,
                        "status": ex.status.value,
                        "started_at": ex.started_at.isoformat() if ex.started_at else None,
                    }
                    for ex in executions if ex.execution_type != "task"
                ]
                job_dict = job.to_dict()
                job_dict["hooks"] = hooks
                job_dict["timeline"] = _job_timeline(
                    job, self._timeline_steps(job, executions), datetime.utcnow()
                )
                return jsonify(job_dict), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>/tasks/<task_id>", methods=["GET"])
        def get_task(job_id: str, task_id: str):
            """Get task details (output, retries, diff)."""
            try:
                task = self.db.get_task(task_id)
                if task is None or task.job_id != job_id:
                    return jsonify({"error": "Task not found"}), 404

                return jsonify(task.to_dict()), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>/plan", methods=["GET"])
        def get_job_plan(job_id: str):
            """Get the job's plan, structured and as raw YAML."""
            try:
                job = self.job_controller.store.load(job_id)
                if job is None:
                    return jsonify({"error": "Job not found"}), 404

                plan_yaml_path = self.job_controller.store.jobs_dir / job_id / "plan.yaml"
                if not plan_yaml_path.exists():
                    return jsonify({"error": "Plan file not found for this job"}), 404

                plan = self.job_controller.store.load_plan(job_id)
                return jsonify({
                    "plan": plan.model_dump(mode="json"),
                    "raw_yaml": plan_yaml_path.read_text(),
                }), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>/logs", methods=["GET"])
        def get_job_logs(job_id: str):
            """Tail the job's log file, optionally filtered by task_id."""
            try:
                job = self._load_job_with_tasks(job_id)
                if job is None:
                    return jsonify({"error": "Job not found"}), 404

                log_path = self.job_controller.store.jobs_dir / job_id / "job.log"
                if not log_path.exists():
                    return jsonify({"records": []}), 200

                limit = request.args.get("limit", default=100, type=int)
                if limit <= 0:
                    limit = 100
                task_filter = request.args.get("task_id")  # None, "all", or a real task id
                hook_filter = request.args.get("hook_name")  # None, "all", or a real hook name

                tail_lines = deque(maxlen=limit)
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            tail_lines.append(line)

                records = []
                for line in tail_lines:
                    try:
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            rec = {"message": line}
                    except json.JSONDecodeError:
                        rec = {"message": line}
                    hook_name = None if rec.get("type") == "task" else rec.get("step")
                    records.append({
                        "timestamp": rec.get("timestamp", ""),
                        "task_id": rec.get("task_id"),
                        "level": rec.get("level", ""),
                        "message": rec.get("message", ""),
                        "hook_name": hook_name,
                    })

                hook_names = sorted({r["hook_name"] for r in records if r["hook_name"]})

                if task_filter and task_filter != "all":
                    records = [r for r in records if r["task_id"] == task_filter]

                if hook_filter and hook_filter != "all":
                    records = [r for r in records if r["hook_name"] == hook_filter]

                return jsonify({"records": records, "hook_names": hook_names}), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>/cancel", methods=["POST"])
        def cancel_job(job_id: str):
            """Cancel a job."""
            try:
                success = self.job_controller.stop_job(job_id)
                if not success:
                    return jsonify({"error": "Failed to cancel job"}), 400

                return jsonify({"success": True}), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

    def start(self):
        """Start the Flask server in a separate thread (non-blocking)."""
        if self.server_thread is not None and self.server_thread.is_alive():
            return

        def run_server():
            """Run the server in a thread."""
            self.app.run(
                host="0.0.0.0",
                port=self.port,
                debug=False,
                use_reloader=False,
            )

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

    def stop(self):
        """Gracefully shut down the server."""
        # ponytail: daemon thread dies with the process; no explicit shutdown
        # hook on Flask's dev server. Revisit if start() moves to a real WSGI server.
        pass
