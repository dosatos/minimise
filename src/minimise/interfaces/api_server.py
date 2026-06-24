"""REST API server exposing read-only job/task state over HTTP."""

import threading
from typing import Optional

from flask import Flask, jsonify, request
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
        job = self.db.get_job(job_id)
        if job is not None:
            job.tasks = self.db.list_tasks_for_job(job_id)
        return job

    def _register_routes(self):
        """Register all REST API routes."""

        @self.app.route("/jobs", methods=["GET"])
        def get_jobs():
            """Get all jobs."""
            try:
                jobs = self.db.list_jobs()
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

                return jsonify(job.to_dict()), 200
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
