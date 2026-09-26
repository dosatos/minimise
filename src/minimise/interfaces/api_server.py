"""REST API server exposing read-only job/task state over HTTP."""

import json
import threading
from collections import deque
from typing import Optional

import markdown
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from minimise.interfaces.loop_views import register_loop_routes
from minimise.models import Job, Plan
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
