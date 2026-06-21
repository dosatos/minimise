import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from minimise.models import Job, Task, JobStatus, TaskStatus

class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_db(self):
        """Create database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_path TEXT,
                base_commit TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                output TEXT,
                retries INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                diff_path TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS diffs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                diff_path TEXT NOT NULL,
                file_count INTEGER,
                lines_added INTEGER,
                lines_removed INTEGER,
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
        """)

        conn.commit()
        conn.close()

    def create_job(self, job: Job) -> None:
        """Insert a new job."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO jobs (id, name, status, plan_path, base_commit, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (job.id, job.name, job.status.value, job.plan_path, job.base_commit, job.created_at.isoformat()))
        conn.commit()
        conn.close()

    def get_job(self, job_id: str) -> Optional[Job]:
        """Fetch a job by ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return Job(
            id=row['id'],
            name=row['name'],
            status=JobStatus(row['status']),
            plan_path=row['plan_path'],
            base_commit=row['base_commit'],
            created_at=datetime.fromisoformat(row['created_at']),
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
        )

    def list_jobs(self) -> List[Job]:
        """Fetch all jobs."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        return [
            Job(
                id=row['id'],
                name=row['name'],
                status=JobStatus(row['status']),
                plan_path=row['plan_path'],
                base_commit=row['base_commit'],
                created_at=datetime.fromisoformat(row['created_at']),
                started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
                completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            )
            for row in rows
        ]

    def update_job_status(self, job_id: str, status: JobStatus, started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None) -> None:
        """Update job status."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE jobs SET status = ?, started_at = ?, completed_at = ? WHERE id = ?
        """, (status.value, started_at.isoformat() if started_at else None, completed_at.isoformat() if completed_at else None, job_id))
        conn.commit()
        conn.close()

    def create_task(self, task: Task) -> None:
        """Insert a new task."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (id, job_id, name, description, status, output, retries, created_at, started_at, completed_at, diff_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task.id, task.job_id, task.name, task.description, task.status.value, task.output, task.retries,
              task.created_at.isoformat(), task.started_at.isoformat() if task.started_at else None,
              task.completed_at.isoformat() if task.completed_at else None, task.diff_path))
        conn.commit()
        conn.close()

    def update_task_status(self, task_id: str, status: TaskStatus, output: Optional[str] = None, retries: int = 0, completed_at: Optional[datetime] = None) -> None:
        """Update task status."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks SET status = ?, output = ?, retries = ?, completed_at = ? WHERE id = ?
        """, (status.value, output, retries, completed_at.isoformat() if completed_at else None, task_id))
        conn.commit()
        conn.close()

    def get_task(self, task_id: str) -> Optional[Task]:
        """Fetch a task by ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return Task(
            id=row['id'],
            job_id=row['job_id'],
            name=row['name'],
            description=row['description'],
            status=TaskStatus(row['status']),
            output=row['output'],
            retries=row['retries'],
            created_at=datetime.fromisoformat(row['created_at']),
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            diff_path=row['diff_path'],
        )

    def list_tasks_for_job(self, job_id: str) -> List[Task]:
        """Fetch all tasks for a job."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE job_id = ? ORDER BY created_at", (job_id,))
        rows = cursor.fetchall()
        conn.close()

        return [
            Task(
                id=row['id'],
                job_id=row['job_id'],
                name=row['name'],
                description=row['description'],
                status=TaskStatus(row['status']),
                output=row['output'],
                retries=row['retries'],
                created_at=datetime.fromisoformat(row['created_at']),
                started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
                completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
                diff_path=row['diff_path'],
            )
            for row in rows
        ]

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and all its associated tasks."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM tasks WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

        conn.commit()
        rows_deleted = cursor.rowcount
        conn.close()

        return rows_deleted > 0

    def resolve_job_id(self, job_id_or_prefix: str) -> Optional[str]:
        """Resolve a job ID, supporting both full IDs and prefixes (e.g., first 8 chars)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM jobs WHERE id = ? OR id LIKE ?", (job_id_or_prefix, f"{job_id_or_prefix}%"))
        row = cursor.fetchone()
        conn.close()

        return row[0] if row else None
