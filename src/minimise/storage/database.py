import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from minimise.models import Job, Task, Execution, JobStatus, TaskStatus


def _column_names(cursor, table: str) -> List[str]:
    """Return the column names of a table (PRAGMA table_info name field)."""
    cursor.execute(f"PRAGMA table_info({table})")
    return [column[1] for column in cursor.fetchall()]


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_job(row: sqlite3.Row) -> Job:
    keys = row.keys()
    return Job(
        id=row['id'],
        name=row['name'],
        status=JobStatus(row['status']),
        plan_path=row['plan_path'],
        base_commit=row['base_commit'],
        created_at=datetime.fromisoformat(row['created_at']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        # sqlite3.Row raises KeyError (not IndexError) on a missing key, so guard
        # by presence rather than catching the wrong exception.
        pid=row['pid'] if 'pid' in keys else None,
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    keys = row.keys()
    dur = row['estimated_duration_min'] if 'estimated_duration_min' in keys else None
    return Task(
        id=row['id'],
        job_id=row['job_id'],
        name=row['name'],
        description=row['description'],
        status=TaskStatus(row['status']),
        output=row['output'],
        retries=row['retries'],
        created_at=datetime.fromisoformat(row['created_at']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        diff_path=row['diff_path'],
        base_commit=row['base_commit'] if 'base_commit' in keys else None,
        goal=row['goal'] if 'goal' in keys else None,
        estimated_duration_min=dur if dur is not None else 5,
    )


def _row_to_execution(row: sqlite3.Row) -> Execution:
    return Execution(
        task_id=row['task_id'],
        attempt=row['attempt'],
        job_id=row['job_id'],
        execution_type=row['execution_type'],
        status=TaskStatus(row['status']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        output=row['output'],
        diff_path=row['diff_path'],
        commit_sha=row['commit_sha'],
    )


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
        columns = _column_names(cursor, "jobs")
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
                estimated_duration_min INTEGER NOT NULL DEFAULT 5,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        """)

        # Add missing columns for existing databases (read schema once).
        columns = _column_names(cursor, "tasks")
        if 'base_commit' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN base_commit TEXT")
        if 'goal' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN goal TEXT")
        if 'estimated_duration_min' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN estimated_duration_min INTEGER DEFAULT NULL")

        # Backfill legacy NULL durations and enforce NOT NULL. init_db uses a bare
        # connection with the default isolation_level (an implicit transaction is
        # already open), so a bare BEGIN would fail; use a SAVEPOINT for atomicity.
        cursor.execute("SAVEPOINT dur_migration")
        try:
            cursor.execute(
                "UPDATE tasks SET estimated_duration_min = 5 WHERE estimated_duration_min IS NULL"
            )
            # Enforce NOT NULL via table rebuild only if the live column still allows NULL.
            cursor.execute("PRAGMA table_info(tasks)")
            dur = next((c for c in cursor.fetchall() if c[1] == "estimated_duration_min"), None)
            if dur is not None and dur[3] == 0:  # notnull flag is 0 -> needs rebuild
                cursor.execute("""
                    CREATE TABLE tasks_new (
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
                        estimated_duration_min INTEGER NOT NULL DEFAULT 5,
                        FOREIGN KEY(job_id) REFERENCES jobs(id)
                    )
                """)
                # Derive the column list from the NEW table so columns stay aligned.
                cursor.execute("PRAGMA table_info(tasks_new)")
                col_list = ", ".join(c[1] for c in cursor.fetchall())
                cursor.execute(f"INSERT INTO tasks_new ({col_list}) SELECT {col_list} FROM tasks")
                cursor.execute("DROP TABLE tasks")
                cursor.execute("ALTER TABLE tasks_new RENAME TO tasks")
            cursor.execute("RELEASE SAVEPOINT dur_migration")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT dur_migration")
            raise

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

        # ponytail: CREATE IF NOT EXISTS like every other table — init_db() runs
        # on EVERY cli command, so an unconditional DROP here wipes executions on
        # every invocation. Old-shape dev tables are dropped once, by hand.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                task_id TEXT,
                execution_type TEXT NOT NULL DEFAULT 'task',
                attempt INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                output TEXT,
                diff_path TEXT,
                commit_sha TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
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

        return _row_to_job(row)

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

        return [_row_to_job(row) for row in rows]

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

        return _row_to_task(row)

    def list_tasks_for_job(self, job_id: str) -> List[Task]:
        """Fetch all tasks for a job."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE job_id = ? ORDER BY created_at", (job_id,))
        rows = cursor.fetchall()
        conn.close()

        return [_row_to_task(row) for row in rows]

    def save_execution(self, execution: Execution) -> None:
        """Insert or replace an execution row (latest-wins, keyed by execution_id)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO executions
                (execution_id, job_id, task_id, execution_type, attempt, status,
                 started_at, completed_at, output, diff_path, commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (execution.execution_id, execution.job_id, execution.task_id,
              execution.execution_type, execution.attempt, execution.status.value,
              execution.started_at.isoformat() if execution.started_at else None,
              execution.completed_at.isoformat() if execution.completed_at else None,
              execution.output, execution.diff_path, execution.commit_sha))
        conn.commit()
        conn.close()

    def list_executions_for_task(self, task_id: str) -> List[Execution]:
        """Fetch a task's executions in attempt order."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM executions WHERE task_id = ? ORDER BY attempt", (task_id,))
        rows = cursor.fetchall()
        conn.close()
        return [_row_to_execution(row) for row in rows]

    def list_executions_for_job(self, job_id: str) -> List[Execution]:
        """Fetch all of a job's executions in timeline order (the timeline query)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM executions WHERE job_id = ? ORDER BY started_at, execution_id",
            (job_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [_row_to_execution(row) for row in rows]

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

        # Delete by job_id directly so plan-hook rows (task_id NULL) are removed too.
        cursor.execute("DELETE FROM executions WHERE job_id = ?", (job_id,))
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
