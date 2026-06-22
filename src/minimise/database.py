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
                completed_at TEXT,
                pid INTEGER
            )
        """)

        # Add pid column if it doesn't exist (migration for existing databases)
        cursor.execute("PRAGMA table_info(jobs)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'pid' not in columns:
            cursor.execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")

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
                base_commit TEXT,
                goal TEXT,
                estimated_duration_min INTEGER DEFAULT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        """)

        # Add base_commit column if it doesn't exist (migration for existing databases)
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'base_commit' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN base_commit TEXT")

        # Add goal column if it doesn't exist (migration for existing databases)
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'goal' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN goal TEXT")

        # Add estimated_duration_min column if it doesn't exist (migration for existing databases)
        cursor.execute("PRAGMA table_info(tasks)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'estimated_duration_min' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN estimated_duration_min INTEGER DEFAULT NULL")

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
            INSERT INTO jobs (id, name, status, plan_path, base_commit, created_at, pid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (job.id, job.name, job.status.value, job.plan_path, job.base_commit, job.created_at.isoformat(), job.pid))
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

        try:
            pid = row['pid']
        except IndexError:
            pid = None

        return Job(
            id=row['id'],
            name=row['name'],
            status=JobStatus(row['status']),
            plan_path=row['plan_path'],
            base_commit=row['base_commit'],
            created_at=datetime.fromisoformat(row['created_at']),
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            pid=pid,
        )

    def list_jobs(self, limit: Optional[int] = None) -> List[Job]:
        """Fetch jobs with optional limit."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if limit is not None:
            cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        jobs = []
        for row in rows:
            try:
                pid = row['pid']
            except IndexError:
                pid = None
            jobs.append(Job(
                id=row['id'],
                name=row['name'],
                status=JobStatus(row['status']),
                plan_path=row['plan_path'],
                base_commit=row['base_commit'],
                created_at=datetime.fromisoformat(row['created_at']),
                started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
                completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
                pid=pid,
            ))
        return jobs

    def update_job_status(self, job_id: str, status: JobStatus, started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None, pid: Optional[int] = None) -> None:
        """Update job status.

        Only updates fields that are explicitly provided. If started_at or completed_at
        are None, their existing database values are preserved.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Build dynamic UPDATE statement to only update provided fields
        update_fields = ["status = ?"]
        params = [status.value]

        if started_at is not None:
            update_fields.append("started_at = ?")
            params.append(started_at.isoformat())

        if completed_at is not None:
            update_fields.append("completed_at = ?")
            params.append(completed_at.isoformat())

        if pid is not None:
            update_fields.append("pid = ?")
            params.append(pid)

        params.append(job_id)

        query = f"UPDATE jobs SET {', '.join(update_fields)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
        conn.close()

    def create_task(self, task: Task) -> None:
        """Insert a new task."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (id, job_id, name, description, status, output, retries, created_at, started_at, completed_at, diff_path, base_commit, goal, estimated_duration_min)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (task.id, task.job_id, task.name, task.description, task.status.value, task.output, task.retries,
              task.created_at.isoformat(), task.started_at.isoformat() if task.started_at else None,
              task.completed_at.isoformat() if task.completed_at else None, task.diff_path, task.base_commit, task.goal, task.estimated_duration_min))
        conn.commit()
        conn.close()

    def update_task_status(self, task_id: str, status: TaskStatus, output: Optional[str] = None, retries: int = 0, started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None) -> None:
        """Update task status.

        Only updates fields that are explicitly provided. If started_at or completed_at
        are None, their existing database values are preserved.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Build dynamic UPDATE statement to only update provided fields
        update_fields = ["status = ?", "retries = ?"]
        params = [status.value, retries]

        # Always update output if provided (even if None)
        update_fields.append("output = ?")
        params.append(output)

        if started_at is not None:
            update_fields.append("started_at = ?")
            params.append(started_at.isoformat())

        if completed_at is not None:
            update_fields.append("completed_at = ?")
            params.append(completed_at.isoformat())

        params.append(task_id)

        query = f"UPDATE tasks SET {', '.join(update_fields)} WHERE id = ?"
        cursor.execute(query, params)
        conn.commit()
        conn.close()

    def update_task(self, task: Task) -> None:
        """Update an entire task object.

        Updates all fields of the task in the database.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks SET
                job_id = ?,
                name = ?,
                description = ?,
                status = ?,
                output = ?,
                retries = ?,
                created_at = ?,
                started_at = ?,
                completed_at = ?,
                diff_path = ?,
                base_commit = ?,
                goal = ?,
                estimated_duration_min = ?
            WHERE id = ?
        """, (task.job_id, task.name, task.description, task.status.value, task.output, task.retries,
              task.created_at.isoformat(), task.started_at.isoformat() if task.started_at else None,
              task.completed_at.isoformat() if task.completed_at else None, task.diff_path, task.base_commit, task.goal, task.estimated_duration_min, task.id))
        conn.commit()
        conn.close()

    def update_task_diff_path(self, task_id: str, diff_path: str) -> None:
        """Update only the diff_path for a task.

        Preserves all other fields unchanged.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET diff_path = ? WHERE id = ?", (diff_path, task_id))
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
            base_commit=row['base_commit'] if 'base_commit' in row.keys() else None,
            goal=row['goal'] if 'goal' in row.keys() else None,
            estimated_duration_min=row['estimated_duration_min'] if 'estimated_duration_min' in row.keys() else None,
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
                base_commit=row['base_commit'] if 'base_commit' in row.keys() else None,
                goal=row['goal'] if 'goal' in row.keys() else None,
                estimated_duration_min=row['estimated_duration_min'] if 'estimated_duration_min' in row.keys() else None,
            )
            for row in rows
        ]

    def delete_job(self, job_id: str, jobs_dir: Path = None) -> bool:
        """Delete a job and all its associated tasks, including disk artifacts.

        Args:
            job_id: ID of the job to delete
            jobs_dir: Directory where job artifacts are stored (optional)

        Returns:
            True if deletion was successful, False otherwise
        """
        import shutil

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM tasks WHERE job_id = ?", (job_id,))
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

        conn.commit()
        rows_deleted = cursor.rowcount
        conn.close()

        # Remove job directory from disk if it exists
        if jobs_dir:
            job_dir = jobs_dir / job_id
            if job_dir.exists():
                shutil.rmtree(job_dir)

        return rows_deleted > 0

    def resolve_job_id(self, job_id_or_prefix: str) -> Optional[str]:
        """Resolve a job ID, supporting both full IDs and prefixes (e.g., first 8 chars).

        Returns the job ID if exactly one match found, None otherwise.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM jobs WHERE id = ? OR id LIKE ?", (job_id_or_prefix, f"{job_id_or_prefix}%"))
        rows = cursor.fetchall()
        conn.close()

        if len(rows) == 1:
            return rows[0][0]
        return None
