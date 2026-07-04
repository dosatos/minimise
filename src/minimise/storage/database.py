import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from minimise.models import Job, Task, Execution, Loop, LoopStep, JobStatus, TaskStatus


_UNSET = object()  # sentinel: "field not provided" vs an explicit None (clear it)


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
        retries=row['retries'],
        created_at=datetime.fromisoformat(row['created_at']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        diff_path=row['diff_path'],
        base_commit=row['base_commit'] if 'base_commit' in keys else None,
        goal=row['goal'] if 'goal' in keys else None,
        assignee=row['assignee'] if 'assignee' in keys else None,
        estimated_duration_min=dur if dur is not None else 5,
    )


def _row_to_execution(row: sqlite3.Row) -> Execution:
    keys = row.keys()
    return Execution(
        task_id=row['task_id'],
        attempt=row['attempt'],
        job_id=row['job_id'],
        execution_type=row['execution_type'],
        status=TaskStatus(row['status']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        diff_path=row['diff_path'],
        commit_sha=row['commit_sha'],
        hook_name=row['hook_name'] if 'hook_name' in keys else None,
        exit_reason=row['exit_reason'] if 'exit_reason' in keys else None,
    )


def _row_to_loop(row: sqlite3.Row) -> Loop:
    keys = row.keys()
    return Loop(
        loop_id=row['loop_id'],
        name=row['name'],
        status=JobStatus(row['status']),
        plan_path=row['plan_path'],
        max_iterations=row['max_iterations'],
        created_at=datetime.fromisoformat(row['created_at']),
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
        pid=row['pid'] if 'pid' in keys else None,
    )


def _row_to_loop_step(row: sqlite3.Row) -> LoopStep:
    keys = row.keys()
    return LoopStep(
        step_id=row['step_id'],
        loop_id=row['loop_id'],
        iteration=row['iteration'],
        step_type=row['step_type'],
        dimension=row['dimension'] if 'dimension' in keys else None,
        status=TaskStatus(row['status']),
        retries=row['retries'],
        started_at=_dt(row['started_at']),
        completed_at=_dt(row['completed_at']),
    )


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        """One connection for several writes — commit on success, rollback on error.

        Pass the yielded ``conn`` into any mutator's ``conn=`` param to enlist it:

            with db.transaction() as conn:
                db.update_task_status(..., conn=conn)
                db.save_execution(..., conn=conn)   # both land, or neither

        Reads (get_*/list_*) run on their own connection, so inside a transaction
        they see only ALREADY-COMMITTED rows — don't expect to read this txn's own
        uncommitted writes back through a separate method call.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _write(self, conn: Optional[sqlite3.Connection]):
        """Yield a cursor for a single mutator.

        If ``conn`` is None the mutator owns its connection (open/commit/close);
        if a ``conn`` is passed it enlists in that open transaction and leaves the
        commit to whoever opened it.
        """
        if conn is not None:
            yield conn.cursor()
            return
        own = sqlite3.connect(self.db_path)
        try:
            yield own.cursor()
            own.commit()
        except Exception:
            own.rollback()
            raise
        finally:
            own.close()

    def _query(self, sql: str, params=()) -> List[sqlite3.Row]:
        """Run a read and return rows as sqlite3.Row (own connection, always closed)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    SCHEMA_VERSION = 6

    def init_db(self):
        """Create/migrate the schema once per DB.

        ``PRAGMA user_version`` is 0 on a fresh or pre-versioning database and
        SCHEMA_VERSION once we've run. init_db() fires on EVERY cli command, so
        this guard makes all but the first invocation a single PRAGMA read.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if cursor.execute("PRAGMA user_version").fetchone()[0] == self.SCHEMA_VERSION:
            conn.close()
            return

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
                retries INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                diff_path TEXT,
                base_commit TEXT,
                goal TEXT,
                assignee TEXT,
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
        if 'assignee' not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN assignee TEXT")
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
                        retries INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        diff_path TEXT,
                        base_commit TEXT,
                        goal TEXT,
                        assignee TEXT,
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
                diff_path TEXT,
                commit_sha TEXT,
                hook_name TEXT,
                exit_reason TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
        """)

        # Add hook_name for existing executions tables (no drop/backfill).
        if 'hook_name' not in _column_names(cursor, "executions"):
            cursor.execute("ALTER TABLE executions ADD COLUMN hook_name TEXT")
        if 'exit_reason' not in _column_names(cursor, "executions"):
            cursor.execute("ALTER TABLE executions ADD COLUMN exit_reason TEXT")

        # v4: retire the free-text `output` column — job.log is the sole narration
        # store. Two guarded table rebuilds drop it from live DBs (SQLite has no
        # DROP COLUMN before 3.35); the col list comes from the *_new schema, so
        # `output` is naturally excluded and columns stay aligned.
        self._drop_output_column(cursor, "executions", """
            CREATE TABLE executions_new (
                execution_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                task_id TEXT,
                execution_type TEXT NOT NULL DEFAULT 'task',
                attempt INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                diff_path TEXT,
                commit_sha TEXT,
                hook_name TEXT,
                exit_reason TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
        """)
        self._drop_output_column(cursor, "tasks", """
            CREATE TABLE tasks_new (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                retries INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                diff_path TEXT,
                base_commit TEXT,
                goal TEXT,
                assignee TEXT,
                estimated_duration_min INTEGER NOT NULL DEFAULT 5,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
        """)

        # v6: additive loop tables — pure CREATE IF NOT EXISTS, no rebuilds/backfills.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS loops (
                loop_id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                plan_path TEXT,
                max_iterations INTEGER,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                pid INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS loop_steps (
                step_id TEXT PRIMARY KEY,
                loop_id TEXT,
                iteration INTEGER,
                step_type TEXT,
                dimension TEXT,
                status TEXT,
                retries INTEGER,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(loop_id) REFERENCES loops(loop_id)
            )
        """)

        cursor.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        conn.commit()
        conn.close()

    @staticmethod
    def _drop_output_column(cursor, table: str, create_new_sql: str) -> None:
        """Rebuild ``table`` without its `output` column, if it still has one.

        Follows the SAVEPOINT table-rebuild precedent: create <table>_new (with
        the desired schema, no `output`), copy the shared columns derived from
        the new table's PRAGMA, drop the old, rename. No-op once `output` is gone.
        """
        if 'output' not in _column_names(cursor, table):
            return
        cursor.execute(f"SAVEPOINT drop_output_{table}")
        try:
            cursor.execute(create_new_sql)
            cursor.execute(f"PRAGMA table_info({table}_new)")
            col_list = ", ".join(c[1] for c in cursor.fetchall())
            cursor.execute(f"INSERT INTO {table}_new ({col_list}) SELECT {col_list} FROM {table}")
            cursor.execute(f"DROP TABLE {table}")
            cursor.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            cursor.execute(f"RELEASE SAVEPOINT drop_output_{table}")
        except Exception:
            cursor.execute(f"ROLLBACK TO SAVEPOINT drop_output_{table}")
            raise

    def create_job(self, job: Job, conn: Optional[sqlite3.Connection] = None) -> None:
        """Insert a new job."""
        with self._write(conn) as cursor:
            cursor.execute("""
                INSERT INTO jobs (id, name, status, plan_path, base_commit, created_at, pid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (job.id, job.name, job.status.value, job.plan_path, job.base_commit, job.created_at.isoformat(), job.pid))

    def create_job_with_tasks(self, job: Job, tasks: List[Task]) -> None:
        """Insert a job and all its tasks in one transaction — all or nothing."""
        with self.transaction() as conn:
            self.create_job(job, conn=conn)
            for task in tasks:
                self.create_task(task, conn=conn)

    def get_job(self, job_id: str) -> Optional[Job]:
        """Fetch a job by ID."""
        rows = self._query("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return _row_to_job(rows[0]) if rows else None

    def list_jobs(self, limit: Optional[int] = None) -> List[Job]:
        """Fetch jobs with optional limit."""
        if limit is not None:
            rows = self._query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            rows = self._query("SELECT * FROM jobs ORDER BY created_at DESC")
        return [_row_to_job(row) for row in rows]

    def update_job_status(self, job_id: str, status: JobStatus, started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None, pid: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> None:
        """Update job status.

        Only updates fields that are explicitly provided. If started_at or completed_at
        are None, their existing database values are preserved.
        """
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
        with self._write(conn) as cursor:
            cursor.execute(query, params)

    def create_task(self, task: Task, conn: Optional[sqlite3.Connection] = None) -> None:
        """Insert a new task."""
        with self._write(conn) as cursor:
            cursor.execute("""
                INSERT INTO tasks (id, job_id, name, description, status, retries, created_at, started_at, completed_at, diff_path, base_commit, goal, assignee, estimated_duration_min)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (task.id, task.job_id, task.name, task.description, task.status.value, task.retries,
                  task.created_at.isoformat(), task.started_at.isoformat() if task.started_at else None,
                  task.completed_at.isoformat() if task.completed_at else None, task.diff_path, task.base_commit, task.goal, task.assignee, task.estimated_duration_min))

    def update_task_status(self, task_id: str, status: TaskStatus, retries: int = 0, started_at: Optional[datetime] = None, completed_at=_UNSET, conn: Optional[sqlite3.Connection] = None) -> None:
        """Update task status.

        Only updates fields that are explicitly provided. If started_at is None,
        its existing value is preserved. completed_at defaults to _UNSET (preserve);
        pass an explicit None to clear it (e.g. re-running a task on resume).
        """
        # Build dynamic UPDATE statement to only update provided fields
        update_fields = ["status = ?", "retries = ?"]
        params = [status.value, retries]

        if started_at is not None:
            update_fields.append("started_at = ?")
            params.append(started_at.isoformat())

        if completed_at is not _UNSET:
            update_fields.append("completed_at = ?")
            params.append(completed_at.isoformat() if completed_at is not None else None)

        params.append(task_id)

        query = f"UPDATE tasks SET {', '.join(update_fields)} WHERE id = ?"
        with self._write(conn) as cursor:
            cursor.execute(query, params)

    def update_task(self, task: Task, conn: Optional[sqlite3.Connection] = None) -> None:
        """Update an entire task object.

        Updates all fields of the task in the database.
        """
        with self._write(conn) as cursor:
            cursor.execute("""
                UPDATE tasks SET
                    job_id = ?,
                    name = ?,
                    description = ?,
                    status = ?,
                    retries = ?,
                    created_at = ?,
                    started_at = ?,
                    completed_at = ?,
                    diff_path = ?,
                    base_commit = ?,
                    goal = ?,
                    assignee = ?,
                    estimated_duration_min = ?
                WHERE id = ?
            """, (task.job_id, task.name, task.description, task.status.value, task.retries,
                  task.created_at.isoformat(), task.started_at.isoformat() if task.started_at else None,
                  task.completed_at.isoformat() if task.completed_at else None, task.diff_path, task.base_commit, task.goal, task.assignee, task.estimated_duration_min, task.id))

    def update_task_diff_path(self, task_id: str, diff_path: str, conn: Optional[sqlite3.Connection] = None) -> None:
        """Update only the diff_path for a task.

        Preserves all other fields unchanged.
        """
        with self._write(conn) as cursor:
            cursor.execute("UPDATE tasks SET diff_path = ? WHERE id = ?", (diff_path, task_id))

    def get_task(self, task_id: str) -> Optional[Task]:
        """Fetch a task by ID."""
        rows = self._query("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_task(rows[0]) if rows else None

    def list_tasks_for_job(self, job_id: str) -> List[Task]:
        """Fetch all tasks for a job."""
        rows = self._query("SELECT * FROM tasks WHERE job_id = ? ORDER BY created_at", (job_id,))
        return [_row_to_task(row) for row in rows]

    def save_execution(self, execution: Execution, conn: Optional[sqlite3.Connection] = None) -> None:
        """Insert or replace an execution row (latest-wins, keyed by execution_id)."""
        with self._write(conn) as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO executions
                    (execution_id, job_id, task_id, execution_type, attempt, status,
                     started_at, completed_at, diff_path, commit_sha, hook_name,
                     exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (execution.execution_id, execution.job_id, execution.task_id,
                  execution.execution_type, execution.attempt, execution.status.value,
                  execution.started_at.isoformat() if execution.started_at else None,
                  execution.completed_at.isoformat() if execution.completed_at else None,
                  execution.diff_path, execution.commit_sha,
                  execution.hook_name, execution.exit_reason))

    def list_executions_for_task(self, task_id: str) -> List[Execution]:
        """Fetch a task's executions in attempt order."""
        rows = self._query("SELECT * FROM executions WHERE task_id = ? ORDER BY attempt", (task_id,))
        return [_row_to_execution(row) for row in rows]

    def list_executions_for_job(self, job_id: str) -> List[Execution]:
        """Fetch all of a job's executions in timeline order (the timeline query)."""
        rows = self._query(
            "SELECT * FROM executions WHERE job_id = ? ORDER BY started_at, execution_id",
            (job_id,),
        )
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
        rows = self._query(
            "SELECT id FROM jobs WHERE id = ? OR id LIKE ?",
            (job_id_or_prefix, f"{job_id_or_prefix}%"),
        )
        return rows[0][0] if len(rows) == 1 else None

    def create_loop(self, loop: Loop, conn: Optional[sqlite3.Connection] = None) -> None:
        """Insert a new loop."""
        with self._write(conn) as cursor:
            cursor.execute("""
                INSERT INTO loops (loop_id, name, status, plan_path, max_iterations, created_at, started_at, completed_at, pid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (loop.loop_id, loop.name, loop.status.value, loop.plan_path, loop.max_iterations,
                  loop.created_at.isoformat(),
                  loop.started_at.isoformat() if loop.started_at else None,
                  loop.completed_at.isoformat() if loop.completed_at else None, loop.pid))

    def get_loop(self, loop_id: str) -> Optional[Loop]:
        """Fetch a loop by ID."""
        rows = self._query("SELECT * FROM loops WHERE loop_id = ?", (loop_id,))
        return _row_to_loop(rows[0]) if rows else None

    def list_loops(self, limit: Optional[int] = None) -> List[Loop]:
        """Fetch loops with optional limit."""
        if limit is not None:
            rows = self._query("SELECT * FROM loops ORDER BY created_at DESC LIMIT ?", (limit,))
        else:
            rows = self._query("SELECT * FROM loops ORDER BY created_at DESC")
        return [_row_to_loop(row) for row in rows]

    def update_loop_status(self, loop_id: str, status: Optional[JobStatus] = None, started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None, pid: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> None:
        """Update loop status. Only updates fields that are explicitly provided."""
        update_fields = []
        params = []

        if status is not None:
            update_fields.append("status = ?")
            params.append(status.value)
        if started_at is not None:
            update_fields.append("started_at = ?")
            params.append(started_at.isoformat())
        if completed_at is not None:
            update_fields.append("completed_at = ?")
            params.append(completed_at.isoformat())
        if pid is not None:
            update_fields.append("pid = ?")
            params.append(pid)

        if not update_fields:
            return

        params.append(loop_id)
        query = f"UPDATE loops SET {', '.join(update_fields)} WHERE loop_id = ?"
        with self._write(conn) as cursor:
            cursor.execute(query, params)

    def create_loop_step(self, step: LoopStep, conn: Optional[sqlite3.Connection] = None) -> None:
        """Insert a new loop step."""
        with self._write(conn) as cursor:
            cursor.execute("""
                INSERT INTO loop_steps (step_id, loop_id, iteration, step_type, dimension, status, retries, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (step.step_id, step.loop_id, step.iteration, step.step_type, step.dimension,
                  step.status.value, step.retries,
                  step.started_at.isoformat() if step.started_at else None,
                  step.completed_at.isoformat() if step.completed_at else None))

    def update_loop_step(self, step_id: str, status: Optional[TaskStatus] = None, retries: Optional[int] = None, started_at: Optional[datetime] = None, completed_at: Optional[datetime] = None, conn: Optional[sqlite3.Connection] = None) -> None:
        """Update a loop step. Only updates fields that are explicitly provided."""
        update_fields = []
        params = []

        if status is not None:
            update_fields.append("status = ?")
            params.append(status.value)
        if retries is not None:
            update_fields.append("retries = ?")
            params.append(retries)
        if started_at is not None:
            update_fields.append("started_at = ?")
            params.append(started_at.isoformat())
        if completed_at is not None:
            update_fields.append("completed_at = ?")
            params.append(completed_at.isoformat())

        if not update_fields:
            return

        params.append(step_id)
        query = f"UPDATE loop_steps SET {', '.join(update_fields)} WHERE step_id = ?"
        with self._write(conn) as cursor:
            cursor.execute(query, params)

    def list_loop_steps(self, loop_id: str) -> List[LoopStep]:
        """Fetch all steps for a loop in execution order."""
        rows = self._query(
            "SELECT * FROM loop_steps WHERE loop_id = ? ORDER BY iteration, started_at",
            (loop_id,),
        )
        return [_row_to_loop_step(row) for row in rows]

    def current_iteration(self, loop_id: str) -> int:
        """Derived iteration numerator: MAX(iteration) across a loop's steps (0 if none)."""
        rows = self._query("SELECT MAX(iteration) FROM loop_steps WHERE loop_id = ?", (loop_id,))
        return rows[0][0] or 0
