"""REST API server with WebSocket support for job/task state exposure."""

import threading
from typing import Optional

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

from minimise.models import Job, Task
from minimise.database import Database
from minimise.job_manager import JobManager


def job_to_dict(job: Job) -> dict:
    """Convert Job object to dictionary for JSON serialization."""
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status.value,
        "plan_path": job.plan_path,
        "base_commit": job.base_commit,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "tasks": [task_to_dict(t) for t in job.tasks] if job.tasks else [],
    }


def task_to_dict(task: Task) -> dict:
    """Convert Task object to dictionary for JSON serialization."""
    return {
        "id": task.id,
        "job_id": task.job_id,
        "name": task.name,
        "description": task.description,
        "status": task.status.value,
        "output": task.output,
        "retries": task.retries,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "diff_path": task.diff_path,
    }


class APIServer:
    """REST API server with WebSocket support for job/task state exposure."""

    def __init__(self, db: Database, job_manager: JobManager, port: int = 5000):
        """
        Initialize the API server.

        Args:
            db: Database instance for accessing job/task data
            job_manager: JobManager instance for job operations
            port: Port to run the server on (default: 5000)
        """
        self.db = db
        self.job_manager = job_manager
        self.port = port
        self.app = Flask(__name__)

        # Enable CORS
        CORS(self.app, resources={r"/*": {"origins": "*"}})

        # Initialize WebSocket support
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")

        self.server_thread: Optional[threading.Thread] = None

        # Set up broadcast callbacks on JobManager
        self.job_manager.on_job_update = self.broadcast_job_update
        self.job_manager.on_task_update = self.broadcast_task_update

        # Register routes
        self._register_routes()

    def _load_job_with_tasks(self, job_id: str) -> Optional[Job]:
        """Fetch a job and attach its task list, or None if it doesn't exist."""
        job = self.db.get_job(job_id)
        if job is not None:
            job.tasks = self.db.list_tasks_for_job(job_id)
        return job

    def _register_routes(self):
        """Register all API routes and WebSocket handlers."""

        # REST API Routes
        @self.app.route("/jobs", methods=["GET"])
        def get_jobs():
            """Get all jobs."""
            try:
                jobs = self.db.list_jobs()
                return jsonify([job_to_dict(job) for job in jobs]), 200
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
                job = self.job_manager.create_job(plan_path)

                if job is None:
                    return jsonify({"error": "Failed to create job"}), 500

                return jsonify(job_to_dict(job)), 201
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>", methods=["GET"])
        def get_job(job_id: str):
            """Get job details with task list."""
            try:
                job = self._load_job_with_tasks(job_id)
                if job is None:
                    return jsonify({"error": "Job not found"}), 404

                return jsonify(job_to_dict(job)), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>/tasks/<task_id>", methods=["GET"])
        def get_task(job_id: str, task_id: str):
            """Get task details (output, retries, diff)."""
            try:
                task = self.db.get_task(task_id)
                if task is None or task.job_id != job_id:
                    return jsonify({"error": "Task not found"}), 404

                return jsonify(task_to_dict(task)), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @self.app.route("/jobs/<job_id>/cancel", methods=["POST"])
        def cancel_job(job_id: str):
            """Cancel a job."""
            try:
                success = self.job_manager.cancel_job(job_id)
                if not success:
                    return jsonify({"error": "Failed to cancel job"}), 400

                return jsonify({"success": True}), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        # WebSocket handlers
        @self.socketio.on("subscribe_job")
        def handle_subscribe_job(data):
            """Subscribe to job status updates."""
            try:
                job_id = data.get("job_id")
                if not job_id:
                    emit("error", {"message": "job_id required"})
                    return

                job = self._load_job_with_tasks(job_id)
                if not job:
                    emit("error", {"message": "Job not found"})
                    return

                # Join room for this job
                join_room(f"job_{job_id}")

                emit("job_update", job_to_dict(job))
            except Exception as e:
                emit("error", {"message": str(e)})

        @self.socketio.on("unsubscribe_job")
        def handle_unsubscribe_job(data):
            """Unsubscribe from job status updates."""
            try:
                job_id = data.get("job_id")
                if not job_id:
                    emit("error", {"message": "job_id required"})
                    return

                leave_room(f"job_{job_id}")
                emit("unsubscribed", {"job_id": job_id})
            except Exception as e:
                emit("error", {"message": str(e)})

    def start(self):
        """Start the Flask server in a separate thread (non-blocking)."""
        if self.server_thread is not None and self.server_thread.is_alive():
            return

        def run_server():
            """Run the server in a thread."""
            self.socketio.run(
                self.app,
                host="0.0.0.0",
                port=self.port,
                debug=False,
                use_reloader=False,
                log_output=False,
                allow_unsafe_werkzeug=True,
            )

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

    def stop(self):
        """Gracefully shut down the server."""
        if self.socketio:
            try:
                self.socketio.stop()
            except RuntimeError:
                # Ignore "Working outside of request context" errors when stopping
                pass

    def broadcast_job_update(self, job_id: str):
        """Broadcast job status update to all subscribers."""
        try:
            job = self._load_job_with_tasks(job_id)
            if job:
                self.socketio.emit(
                    "job_update",
                    job_to_dict(job),
                    room=f"job_{job_id}",
                )
        except Exception as e:
            self.socketio.emit(
                "error",
                {"message": f"Failed to broadcast job update: {str(e)}"},
                room=f"job_{job_id}",
            )

    def broadcast_task_update(self, job_id: str, task_id: str):
        """Broadcast task status update to job subscribers."""
        try:
            task = self.db.get_task(task_id)
            if task:
                self.socketio.emit(
                    "task_update",
                    task_to_dict(task),
                    room=f"job_{job_id}",
                )
        except Exception as e:
            self.socketio.emit(
                "error",
                {"message": f"Failed to broadcast task update: {str(e)}"},
                room=f"job_{job_id}",
            )
