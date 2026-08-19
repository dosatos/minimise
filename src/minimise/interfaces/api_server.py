"""REST API server exposing read-only job/task state over HTTP."""

import json
import threading
from collections import deque
from typing import Optional

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from minimise.models import Job
from minimise.storage.database import Database
from minimise.orchestration.job_controller import JobController


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
        self.port = port
        self.app = Flask(__name__)

        # Enable CORS
        CORS(self.app, resources={r"/*": {"origins": "*"}})

        self.server_thread: Optional[threading.Thread] = None

        # Register routes
        self._register_routes()

    def _load_job_with_tasks(self, job_id: str) -> Optional[Job]:
        """Fetch a job and attach its task list, or None if it doesn't exist."""
        return self.job_controller.store.load(job_id)

    def _load_many_with_tasks(self) -> list[Job]:
        """List jobs with each one's task list attached (store.load_many omits tasks)."""
        jobs = self.job_controller.store.load_many()
        for job in jobs:
            job.tasks = self.db.list_tasks_for_job(job.id)
        return jobs

    def _register_routes(self):
        """Register all REST API routes."""

        @self.app.route("/", methods=["GET"])
        def job_list_page():
            """Server-rendered job list page, polled client-side via /jobs."""
            jobs = self._load_many_with_tasks()
            return render_template("list.html", jobs=jobs)

        @self.app.route("/jobs/<job_id>/view", methods=["GET"])
        def job_detail_page(job_id: str):
            job = self._load_job_with_tasks(job_id)
            if job is None:
                return "Job not found", 404
            return render_template("detail.html", job=job)

        @self.app.route("/jobs", methods=["GET"])
        def get_jobs():
            """Get all jobs, each with its task list attached."""
            try:
                jobs = self._load_many_with_tasks()
                return jsonify([job.to_dict() for job in jobs]), 200
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
