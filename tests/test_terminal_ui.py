"""Tests for terminal_ui module."""

import pytest
from datetime import datetime, timedelta
from minimise.interfaces.terminal_ui import (
    format_duration,
    humanize_duration,
    render_gantt_bar,
    render_execution_table_with_gantt,
    get_status_color,
)
from minimise.models import Job, Task, JobStatus, TaskStatus, Execution


@pytest.fixture
def base_time():
    """Create a consistent base time for testing."""
    return datetime(2026, 1, 15, 12, 0, 0)


@pytest.fixture
def sample_job(base_time):
    """Create a sample job for testing."""
    job = Job(
        id="job-001",
        name="Test Job",
        status=JobStatus.RUNNING,
        plan_path="/path/to/plan.yaml",
        base_commit="abc123",
    )
    job.created_at = base_time
    job.started_at = base_time
    job.completed_at = base_time + timedelta(seconds=10)
    return job


@pytest.fixture
def sample_tasks(base_time):
    """Create sample tasks for testing."""
    tasks = []

    # Task 1: completed quickly (100ms)
    task1 = Task(estimated_duration_min=5, 
        id="task-001",
        job_id="job-001",
        name="Quick Task",
        description="Quick task description",
        status=TaskStatus.COMPLETED,
    )
    task1.started_at = base_time + timedelta(seconds=1)
    task1.completed_at = base_time + timedelta(seconds=1, milliseconds=100)
    tasks.append(task1)

    # Task 2: completed with 1.2s duration
    task2 = Task(estimated_duration_min=5, 
        id="task-002",
        job_id="job-001",
        name="Medium Task",
        description="Medium task description",
        status=TaskStatus.COMPLETED,
    )
    task2.started_at = base_time + timedelta(seconds=2)
    task2.completed_at = base_time + timedelta(seconds=3, milliseconds=200)
    tasks.append(task2)

    # Task 3: completed with 5.5s duration
    task3 = Task(estimated_duration_min=5, 
        id="task-003",
        job_id="job-001",
        name="Long Task",
        description="Long task description",
        status=TaskStatus.COMPLETED,
    )
    task3.started_at = base_time + timedelta(seconds=3.5)
    task3.completed_at = base_time + timedelta(seconds=9)
    tasks.append(task3)

    # Task 4: currently running
    task4 = Task(estimated_duration_min=5, 
        id="task-004",
        job_id="job-001",
        name="Running Task",
        description="Running task description",
        status=TaskStatus.RUNNING,
    )
    task4.started_at = base_time + timedelta(seconds=5)
    task4.completed_at = None
    tasks.append(task4)

    return tasks


class TestHumanizeDuration:
    """Tests for humanize_duration function."""

    # Milliseconds range
    def test_humanize_duration_0ms(self):
        """Test formatting 0 milliseconds."""
        assert humanize_duration(0) == "0ms"

    def test_humanize_duration_500ms(self):
        """Test formatting 500 milliseconds."""
        assert humanize_duration(0.5) == "500ms"

    def test_humanize_duration_999ms(self):
        """Test formatting 999 milliseconds."""
        assert humanize_duration(0.999) == "999ms"

    # Seconds range
    def test_humanize_duration_1_0s(self):
        """Test formatting 1.0 second."""
        assert humanize_duration(1.0) == "1.0s"

    def test_humanize_duration_30_0s(self):
        """Test formatting 30.0 seconds."""
        assert humanize_duration(30.0) == "30.0s"

    def test_humanize_duration_45_5s(self):
        """Test formatting 45.5 seconds."""
        assert humanize_duration(45.5) == "45.5s"

    def test_humanize_duration_59_9s(self):
        """Test formatting 59.9 seconds."""
        assert humanize_duration(59.9) == "59.9s"

    # Minutes range
    def test_humanize_duration_1m_0s(self):
        """Test formatting 1 minute 0 seconds (60s)."""
        assert humanize_duration(60) == "1m 0s"

    def test_humanize_duration_1m_30s(self):
        """Test formatting 1 minute 30 seconds (90s)."""
        assert humanize_duration(90) == "1m 30s"

    def test_humanize_duration_2m_35s(self):
        """Test formatting 2 minutes 35 seconds (155s)."""
        assert humanize_duration(155) == "2m 35s"

    def test_humanize_duration_9m_59s(self):
        """Test formatting 9 minutes 59 seconds (599s)."""
        assert humanize_duration(599) == "9m 59s"

    def test_humanize_duration_10m_0s(self):
        """Test formatting 10 minutes 0 seconds (600s)."""
        assert humanize_duration(600) == "10m 0s"

    # Hours range
    def test_humanize_duration_1h_0m(self):
        """Test formatting 1 hour 0 minutes (3600s)."""
        assert humanize_duration(3600) == "1h 0m"

    def test_humanize_duration_1h_1m(self):
        """Test formatting 1 hour 1 minute (3660s)."""
        assert humanize_duration(3660) == "1h 1m"

    def test_humanize_duration_1h_30m(self):
        """Test formatting 1 hour 30 minutes (5400s)."""
        assert humanize_duration(5400) == "1h 30m"

    def test_humanize_duration_23h_59m(self):
        """Test formatting 23 hours 59 minutes (86340s)."""
        assert humanize_duration(86340) == "23h 59m"

    # Days range
    def test_humanize_duration_1d_0h_0m(self):
        """Test formatting 1 day 0 hours 0 minutes (86400s)."""
        assert humanize_duration(86400) == "1d 0h 0m"

    def test_humanize_duration_1d_1h_1m(self):
        """Test formatting 1 day 1 hour 1 minute (90061s)."""
        assert humanize_duration(90061) == "1d 1h 1m"

    def test_humanize_duration_2d_0h_0m(self):
        """Test formatting 2 days 0 hours 0 minutes (172800s)."""
        assert humanize_duration(172800) == "2d 0h 0m"

    def test_humanize_duration_2d_12h_30m(self):
        """Test formatting 2 days 12 hours 30 minutes (217800s)."""
        assert humanize_duration(217800) == "2d 12h 30m"

    def test_humanize_duration_10d_5h_23m(self):
        """Test formatting 10 days 5 hours 23 minutes."""
        # 10 * 86400 + 5 * 3600 + 23 * 60 = 864000 + 18000 + 1380 = 883380
        assert humanize_duration(883380) == "10d 5h 23m"


class TestRenderEquivalence:
    """Byte-identical output checks for the refactored modulo and gantt loop."""

    @pytest.mark.parametrize("seconds,expected", [
        (155, "2m 35s"),
        (5400, "1h 30m"),
        (86400, "1d 0h 0m"),
        (217800, "2d 12h 30m"),  # exercises the simplified (total % 3600) path
        (883380, "10d 5h 23m"),
    ])
    def test_humanize_duration_exact(self, seconds, expected):
        assert humanize_duration(seconds) == expected

    @pytest.mark.parametrize("task_secs,expected", [
        (10, "████████████████████████████"),  # full span
        (5, "██████████████░░░░░░░░░░░░░░"),   # first half
        (3, "████████░░░░░░░░░░░░░░░░░░░░"),   # first ~third
    ])
    def test_render_gantt_bar_exact(self, base_time, task_secs, expected):
        job_start = base_time
        job_end = base_time + timedelta(seconds=10)
        result = render_gantt_bar(
            job_start, base_time + timedelta(seconds=task_secs), job_start, job_end
        )
        assert result == expected
        assert len(result) == 28


class TestFormatDuration:
    """Tests for format_duration function."""

    def test_format_duration_100ms(self, base_time):
        """Test formatting 100ms duration."""
        started = base_time
        completed = base_time + timedelta(milliseconds=100)
        result = format_duration(started, completed)
        assert result == "100ms"

    def test_format_duration_1_2_seconds(self, base_time):
        """Test formatting 1.2s duration."""
        started = base_time
        completed = base_time + timedelta(seconds=1, milliseconds=200)
        result = format_duration(started, completed)
        assert result == "1.2s"

    def test_format_duration_5_5_seconds(self, base_time):
        """Test formatting 5.5s duration."""
        started = base_time
        completed = base_time + timedelta(seconds=5, milliseconds=500)
        result = format_duration(started, completed)
        assert result == "5.5s"

    def test_format_duration_no_start_time(self):
        """Test that missing start time returns dash."""
        result = format_duration(None, datetime.utcnow())
        assert result == "—"

    def test_format_duration_no_end_time(self, base_time):
        """Test that missing end time returns dash."""
        result = format_duration(base_time, None)
        assert result == "—"

    def test_format_duration_running_task(self, base_time):
        """Test formatting duration of running task."""
        started = base_time
        now = base_time + timedelta(seconds=0, milliseconds=500)
        result = format_duration(started, None, is_running=True, now=now)
        assert result == "500ms"

    def test_format_duration_running_task_seconds(self, base_time):
        """Test formatting duration of running task in seconds."""
        started = base_time
        now = base_time + timedelta(seconds=2, milliseconds=500)
        result = format_duration(started, None, is_running=True, now=now)
        assert result == "2.5s"


class TestRenderGanttBar:
    """Tests for render_gantt_bar function."""

    def test_render_gantt_bar_full_job_duration(self, base_time):
        """Test Gantt bar for task that spans entire job."""
        job_start = base_time
        job_end = base_time + timedelta(seconds=10)
        task_start = base_time
        task_end = base_time + timedelta(seconds=10)

        result = render_gantt_bar(task_start, task_end, job_start, job_end)
        assert result == "████████████████████████████"
        assert len(result) == 28

    def test_render_gantt_bar_first_half(self, base_time):
        """Test Gantt bar for task in first half of job."""
        job_start = base_time
        job_end = base_time + timedelta(seconds=10)
        task_start = base_time
        task_end = base_time + timedelta(seconds=5)

        result = render_gantt_bar(task_start, task_end, job_start, job_end)
        # First half should be filled
        filled = result.count("█")
        assert filled > 0
        assert filled <= 14  # Roughly half

    def test_render_gantt_bar_no_job_start(self, base_time):
        """Test that missing job start returns dash."""
        result = render_gantt_bar(base_time, base_time + timedelta(seconds=5), None, base_time + timedelta(seconds=10))
        assert result == "—"

    def test_render_gantt_bar_no_task_start(self, base_time):
        """Test that missing task start returns dash."""
        job_start = base_time
        job_end = base_time + timedelta(seconds=10)
        result = render_gantt_bar(None, base_time + timedelta(seconds=5), job_start, job_end)
        assert result == "—"

    def test_render_gantt_bar_running_task(self, base_time):
        """Test Gantt bar for running task."""
        job_start = base_time
        job_end = base_time + timedelta(seconds=10)
        task_start = base_time + timedelta(seconds=2)
        now = base_time + timedelta(seconds=7)

        result = render_gantt_bar(task_start, None, job_start, job_end, is_running=True, now=now)
        # Should have some content (mix of empty and filled)
        assert "█" in result or "░" in result
        assert len(result) == 28

    def test_gantt_bar_completed_task_in_running_job(self, base_time):
        """Test Gantt bar for completed task when job is still running.

        This tests the bug fix: a task can be completed while the job is still running
        (job_completed_at is None). The bar should still render for the completed task.
        """
        job_start = base_time
        task_start = base_time + timedelta(seconds=1)
        task_end = base_time + timedelta(seconds=3)
        now = base_time + timedelta(seconds=8)
        # Job is still running, so job_completed_at is None

        result = render_gantt_bar(
            task_start, task_end, job_start, None,
            is_running=False, now=now
        )
        # Task is completed, so should render a bar (not "—")
        assert result != "—"
        assert "█" in result
        assert len(result) == 28

    def test_gantt_bar_running_task_in_running_job(self, base_time):
        """Test Gantt bar for running task when job is still running.

        When both task and job are running, render based on current time.
        """
        job_start = base_time
        task_start = base_time + timedelta(seconds=2)
        now = base_time + timedelta(seconds=7)
        # Both job and task are running

        result = render_gantt_bar(
            task_start, None, job_start, None,
            is_running=True, now=now
        )
        # Should render a bar showing task progress
        assert result != "—"
        assert "█" in result
        assert len(result) == 28

    def test_gantt_bar_completed_job_unchanged(self, base_time):
        """Test that completed jobs still render bars correctly (no regression).

        When job is completed, bars should render as before.
        """
        job_start = base_time
        job_end = base_time + timedelta(seconds=10)
        task_start = base_time + timedelta(seconds=2)
        task_end = base_time + timedelta(seconds=5)

        result = render_gantt_bar(task_start, task_end, job_start, job_end)
        # Should render normally for completed jobs
        assert result != "—"
        assert "█" in result
        assert len(result) == 28


class TestGetStatusColor:
    """Tests for get_status_color function."""

    def test_get_status_color_pending(self):
        """Test color for pending status."""
        assert get_status_color(TaskStatus.PENDING) == "yellow"

    def test_get_status_color_running(self):
        """Test color for running status."""
        assert get_status_color(TaskStatus.RUNNING) == "blue"

    def test_get_status_color_completed(self):
        """Test color for completed status."""
        assert get_status_color(TaskStatus.COMPLETED) == "green"

    def test_get_status_color_failed(self):
        """Test color for failed status."""
        assert get_status_color(TaskStatus.FAILED) == "red"

    def test_get_status_color_stopped(self):
        """Test color for stopped status."""
        assert get_status_color(TaskStatus.STOPPED) == "magenta"

    def test_get_status_color_job_status(self):
        """Test color for job status."""
        assert get_status_color(JobStatus.RUNNING) == "blue"
        assert get_status_color(JobStatus.COMPLETED) == "green"

    def test_get_status_color_string_status(self):
        """Test color for string status."""
        assert get_status_color("running") == "blue"
        assert get_status_color("completed") == "green"

    def test_get_status_color_unknown_status(self):
        """Test color for unknown status defaults to white."""
        assert get_status_color("unknown_status") == "white"


class TestRenderTaskTableWithGantt:
    """Tests for render_execution_table_with_gantt function."""

    def test_render_execution_table_with_gantt_basic(self, sample_job, sample_tasks, base_time):
        """Test that task table renders with all required columns."""
        table = render_execution_table_with_gantt(sample_job, sample_tasks[:3], now=base_time + timedelta(seconds=10))

        # Check that table was created
        assert table is not None
        # Check that table has the expected columns
        assert len(table.columns) == 8
        assert table.columns[0].header == "Task Name"
        assert table.columns[1].header == "Assignee"
        assert table.columns[2].header == "Status"
        assert table.columns[3].header == "Duration"
        assert table.columns[4].header == "Expected"
        assert table.columns[5].header == "Timeline (relative)"
        assert table.columns[6].header == "Type"
        assert table.columns[7].header == "Reason"

    def test_render_execution_table_with_gantt_row_count(self, sample_job, sample_tasks, base_time):
        """Test that table has correct number of rows."""
        table = render_execution_table_with_gantt(sample_job, sample_tasks, now=base_time + timedelta(seconds=10))

        # Table should have rows for each task
        assert len(table.rows) == len(sample_tasks)

    def test_render_execution_table_with_gantt_empty_tasks(self, sample_job):
        """Test that table renders with empty task list."""
        table = render_execution_table_with_gantt(sample_job, [])

        assert table is not None
        assert len(table.rows) == 0
        assert len(table.columns) == 8

    def test_render_execution_table_with_gantt_contains_duration_data(self, sample_job, sample_tasks, base_time):
        """Test that table includes duration information."""
        table = render_execution_table_with_gantt(sample_job, sample_tasks[:1], now=base_time + timedelta(seconds=10))

        # First task has 100ms duration
        assert len(table.rows) == 1
        # The row should have data for each column
        assert table.rows[0] is not None

    def test_render_execution_table_with_gantt_contains_gantt_bars(self, sample_job, sample_tasks, base_time):
        """Test that table includes Gantt bar information."""
        table = render_execution_table_with_gantt(sample_job, sample_tasks[:2], now=base_time + timedelta(seconds=10))

        # Should have 2 rows, each with Gantt bar in last column
        assert len(table.rows) == 2
        for row in table.rows:
            assert row is not None

    def test_render_execution_table_with_gantt_running_task_included(self, sample_job, sample_tasks, base_time):
        """Test that running tasks are included in table."""
        running_task = sample_tasks[3]  # The running task
        table = render_execution_table_with_gantt(sample_job, [running_task], now=base_time + timedelta(seconds=10))

        assert len(table.rows) == 1
        # Should still have data for running task
        assert table.rows[0] is not None

    def test_render_one_row_per_execution(self, sample_job, base_time):
        """A task with 2 executions renders 2 rows with per-execution timing/status."""
        task = Task(
            estimated_duration_min=5,
            id="task-multi",
            job_id="job-001",
            name="Retried Task",
            description="desc",
            status=TaskStatus.COMPLETED,
        )
        # Attempt 0 failed early; attempt 1 completed later. Distinct spans.
        ex0 = Execution(
            task_id="task-multi",
            attempt=0,
            status=TaskStatus.FAILED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        ex1 = Execution(
            task_id="task-multi",
            attempt=1,
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=4),
            completed_at=base_time + timedelta(seconds=9),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [task],
            now=base_time + timedelta(seconds=10),
            executions_by_task={"task-multi": [ex0, ex1]},
        )

        assert len(table.rows) == 2

        # Row labels distinguish attempts
        assert table.columns[0]._cells == ["Retried Task  · try 1", "Retried Task  · try 2"]
        # Status cells read failed then completed (per-execution, not task)
        assert table.columns[2]._cells[0].plain == "failed"
        assert table.columns[2]._cells[1].plain == "completed"
        # Timeline bars differ (computed from each execution's own span)
        assert table.columns[5]._cells[0] != table.columns[5]._cells[1]

    def test_render_no_executions_falls_back_to_single_row(self, sample_job, sample_tasks, base_time):
        """A task with no executions renders exactly one row (today's behavior)."""
        table = render_execution_table_with_gantt(
            sample_job,
            sample_tasks[:1],
            now=base_time + timedelta(seconds=10),
            executions_by_task={},
        )
        assert len(table.rows) == 1
        assert table.columns[0]._cells == ["Quick Task"]

    def test_attempts_only_unchanged(self, sample_job, base_time):
        """Flat executions list of task attempts renders identical per-attempt rows."""
        task = Task(
            estimated_duration_min=5,
            id="task-multi",
            job_id="job-001",
            name="Retried Task",
            description="desc",
            status=TaskStatus.COMPLETED,
        )
        ex0 = Execution(
            task_id="task-multi",
            attempt=0,
            execution_type="task",
            status=TaskStatus.FAILED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        ex1 = Execution(
            task_id="task-multi",
            attempt=1,
            execution_type="task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=4),
            completed_at=base_time + timedelta(seconds=9),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [task],
            now=base_time + timedelta(seconds=10),
            executions=[ex0, ex1],
        )
        assert len(table.rows) == 2
        assert table.columns[0]._cells == ["Retried Task  · try 1", "Retried Task  · try 2"]

    def test_plan_hooks_bracket_task_rows(self, sample_job, base_time):
        """Plan hook rows render in list order around task attempt rows."""
        task = Task(
            estimated_duration_min=5,
            id="task-1",
            job_id="job-001",
            name="Build",
            description="desc",
            status=TaskStatus.COMPLETED,
        )
        pre = Execution(
            task_id=None,
            attempt=0,
            execution_type="pre_plan",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        attempt = Execution(
            task_id="task-1",
            attempt=0,
            execution_type="task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=3),
            completed_at=base_time + timedelta(seconds=5),
        )
        post = Execution(
            task_id=None,
            attempt=0,
            execution_type="post_plan",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=6),
            completed_at=base_time + timedelta(seconds=7),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [task],
            now=base_time + timedelta(seconds=10),
            executions=[pre, attempt, post],
        )
        assert table.columns[0]._cells == [
            "Pre-plan hook",
            "Build  · try 1",
            "Post-plan hook",
        ]

    def test_task_hooks_labelled_with_task_name(self, sample_job, base_time):
        """Per-task hook rows include the resolved task name."""
        task = Task(
            estimated_duration_min=5,
            id="task-1",
            job_id="job-001",
            name="Build",
            description="desc",
            status=TaskStatus.COMPLETED,
        )
        pre = Execution(
            task_id="task-1",
            attempt=0,
            execution_type="pre_task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        attempt = Execution(
            task_id="task-1",
            attempt=0,
            execution_type="task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=3),
            completed_at=base_time + timedelta(seconds=5),
        )
        post = Execution(
            task_id="task-1",
            attempt=0,
            execution_type="post_task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=6),
            completed_at=base_time + timedelta(seconds=7),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [task],
            now=base_time + timedelta(seconds=10),
            executions=[pre, attempt, post],
        )
        assert table.columns[0]._cells == [
            "Pre-task hook  · Build",
            "Build  · try 1",
            "Post-task hook  · Build",
        ]

    def test_hook_row_expected_is_dash(self, sample_job, base_time):
        """Hook rows show '—' in the Expected column."""
        hook = Execution(
            task_id=None,
            attempt=0,
            execution_type="pre_plan",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [],
            now=base_time + timedelta(seconds=10),
            executions=[hook],
        )
        assert table.columns[4]._cells == ["—"]

    def test_executions_none_uses_legacy_path(self, sample_job, sample_tasks, base_time):
        """executions=None with executions_by_task reproduces today's per-attempt rows."""
        task = sample_tasks[0]
        ex0 = Execution(
            task_id=task.id,
            attempt=0,
            execution_type="task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [task],
            now=base_time + timedelta(seconds=10),
            executions=None,
            executions_by_task={task.id: [ex0]},
        )
        assert len(table.rows) == 1
        assert table.columns[0]._cells == ["Quick Task  · try 1"]

    def test_pending_task_rendered_as_placeholder_row(self, sample_job, base_time):
        """A task with no task-type execution shows a PENDING placeholder row in plan order."""
        started = Task(
            estimated_duration_min=5,
            id="task-1",
            job_id="job-001",
            name="Started",
            description="desc",
            status=TaskStatus.COMPLETED,
        )
        pending = Task(
            estimated_duration_min=7,
            id="task-2",
            job_id="job-001",
            name="Not Started",
            description="desc",
            status=TaskStatus.PENDING,
        )
        ex = Execution(
            task_id="task-1",
            attempt=0,
            execution_type="task",
            status=TaskStatus.COMPLETED,
            started_at=base_time + timedelta(seconds=1),
            completed_at=base_time + timedelta(seconds=2),
        )
        table = render_execution_table_with_gantt(
            sample_job,
            [started, pending],
            now=base_time + timedelta(seconds=10),
            executions=[ex],
        )
        assert table.columns[0]._cells == ["Started  · try 1", "Not Started"]
        assert table.columns[2]._cells[1].plain == "pending"
        # Expected column reflects the pending task's estimated duration; bar is empty.
        assert table.columns[4]._cells[1] == "7m 0s"
        assert table.columns[5]._cells[1] == "—"

    def test_all_tasks_pending_with_empty_executions(self, sample_job, base_time):
        """First status call (no executions yet) shows the full plan as PENDING rows in order."""
        tasks = [
            Task(id=f"task-{i}", job_id="job-001", name=f"Task {i}",
                 description="desc", status=TaskStatus.PENDING, estimated_duration_min=i)
            for i in (1, 2, 3)
        ]
        table = render_execution_table_with_gantt(
            sample_job, tasks, now=base_time, executions=[],
        )
        assert table.columns[0]._cells == ["Task 1", "Task 2", "Task 3"]
        assert [c.plain for c in table.columns[2]._cells] == ["pending"] * 3


def test_build_steps_plan_order_with_pending_hook():
    from datetime import datetime
    from minimise.interfaces.terminal_ui import build_steps
    from minimise.models import Plan, Execution, Task, TaskStatus

    plan = Plan.model_validate({
        "name": "P",
        "tasks": [{
            "id": "t1", "name": "Build", "description": "d", "goal": "g",
            "estimated_duration_min": 3,
            "pre_hooks": [{"name": "setup", "shell": "true", "estimated_duration_min": 1}],
            "post_hooks": [{"name": "pytest", "shell": "pytest", "estimated_duration_min": 2}],
        }],
    })
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d",
                  estimated_duration_min=3, goal="g")]
    # only the pre-hook + the task attempt have run; post-hook is pending
    execs = [
        Execution(task_id="task-1", attempt=0, job_id="j1", execution_type="pre_task",
                  hook_name="setup", status=TaskStatus.COMPLETED,
                  started_at=datetime(2026,1,1,0,0,0), completed_at=datetime(2026,1,1,0,0,1)),
        Execution(task_id="task-1", attempt=0, job_id="j1", execution_type="task",
                  status=TaskStatus.COMPLETED,
                  started_at=datetime(2026,1,1,0,0,1), completed_at=datetime(2026,1,1,0,0,5)),
    ]
    steps = build_steps(plan, tasks, execs)
    assert [s.name for s in steps] == ["setup", "Build  · try 1", "pytest"]
    assert steps[0].status == TaskStatus.COMPLETED
    assert steps[2].status == TaskStatus.PENDING  # post-hook not run, drawn from plan
    assert steps[2].estimate == 2


def test_build_steps_brackets_plan_hooks():
    from minimise.interfaces.terminal_ui import build_steps
    from minimise.models import Plan, Task
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "init", "shell": "true", "estimated_duration_min": 1}],
        "post_hooks": [{"name": "deploy", "shell": "true", "estimated_duration_min": 5}],
        "tasks": [{"id": "t1", "name": "Build", "description": "d", "goal": "g",
                   "estimated_duration_min": 3}],
    })
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d",
                  estimated_duration_min=3, goal="g")]
    steps = build_steps(plan, tasks, [])
    assert [s.name for s in steps] == ["init", "Build", "deploy"]  # all PENDING, plan order


def test_type_column_marks_hook_vs_task():
    from minimise.interfaces.terminal_ui import render_execution_table_with_gantt
    from minimise.models import Job, JobStatus, Plan, Task

    job = Job(id="j1", name="J", plan_path="p.yaml", status=JobStatus.RUNNING)
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "init", "shell": "true", "estimated_duration_min": 1}],
        "tasks": [{"id": "t1", "name": "Build", "description": "d", "goal": "g",
                   "estimated_duration_min": 3}],
    })
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d",
                  estimated_duration_min=3, goal="g")]
    table = render_execution_table_with_gantt(job, tasks, plan=plan)
    assert table.columns[6]._cells == ["hook", "task"]


def test_assignee_column_shown_on_plan_projected_path():
    """A plan task with an assignee and no executions (default Step path) shows
    the assignee; the hook row before it stays blank."""
    from minimise.interfaces.terminal_ui import render_execution_table_with_gantt
    from minimise.models import Job, JobStatus, Plan, Task

    job = Job(id="j1", name="J", plan_path="p.yaml", status=JobStatus.RUNNING)
    plan = Plan.model_validate({
        "name": "P",
        "pre_hooks": [{"name": "init", "shell": "true", "estimated_duration_min": 1}],
        "tasks": [{"id": "t1", "name": "Build", "description": "d", "goal": "g",
                   "estimated_duration_min": 3}],
    })
    tasks = [Task(id="task-1", job_id="j1", name="Build", description="d",
                  estimated_duration_min=3, goal="g", assignee="reviewer")]
    table = render_execution_table_with_gantt(job, tasks, plan=plan)
    assert table.columns[1].header == "Assignee"
    assert table.columns[1]._cells == ["", "reviewer"]


def test_reason_column_shows_exit_reason():
    """A failed execution's exit_reason renders under the Reason column."""
    from minimise.interfaces.terminal_ui import render_execution_table_with_gantt
    from minimise.models import Job, JobStatus, Execution, Task, TaskStatus

    job = Job(id="j1", name="J", plan_path="p.yaml", status=JobStatus.RUNNING)
    tasks = [Task(id="t1", job_id="j1", name="Slow", description="d",
                  estimated_duration_min=3, status=TaskStatus.FAILED)]
    ex = Execution(task_id="t1", attempt=0, job_id="j1", status=TaskStatus.FAILED,
                   exit_reason="timeout")
    table = render_execution_table_with_gantt(job, tasks, executions=[ex])

    assert table.columns[7].header == "Reason"
    assert table.columns[7]._cells == ["timeout"]


def test_project_steps_chains_pending_after_completed():
    from minimise.interfaces.terminal_ui import project_steps, Step
    js = datetime(2026, 1, 1, 0, 0, 0)
    # a completed step [0..60s], then a pending step estimated 2 min
    steps = [
        Step(name="a", estimate=1, status=TaskStatus.COMPLETED,
             started_at=js, ended_at=js + timedelta(seconds=60)),
        Step(name="b", estimate=2, status=TaskStatus.PENDING),
    ]
    now = js + timedelta(seconds=60)
    placements, total_secs = project_steps(steps, js, now)
    # pending step starts where the completed one ended
    assert placements[0][2] == 60          # proj end of completed
    assert placements[1][0] == 60          # pending starts at cursor
    assert placements[1][2] == 60 + 120    # + 2min estimate
    assert total_secs == 180


def test_render_projected_bar_solid_then_light():
    from minimise.interfaces.terminal_ui import render_projected_bar
    # actual [0..90], projected to 180, total 180 -> first half solid, rest light
    bar = render_projected_bar(0, 90, 180, total_secs=180, width=28)
    assert len(bar) == 28
    assert "█" in bar and "░" in bar
    assert "╎" not in bar          # now-line removed
    assert bar[0] == "█"
    assert bar[-1] == "░"


def test_render_projected_bar_fills_width_when_complete():
    from minimise.interfaces.terminal_ui import render_projected_bar
    # a fully-actual span across the whole timeline reaches the last column
    bar = render_projected_bar(0, 180, 180, total_secs=180, width=28)
    assert bar[-1] == "█"          # bars fill to the right edge, no trailing gap


def test_project_steps_total_is_elapsed_plus_remaining():
    from minimise.interfaces.terminal_ui import project_steps, Step
    js = datetime(2026, 1, 1, 0, 0, 0)
    now = js + timedelta(seconds=30)
    # running step started at 0, elapsed 30s, est 1min; pending est 1min
    steps = [
        Step(name="a", estimate=1, status=TaskStatus.RUNNING, started_at=js),
        Step(name="b", estimate=1, status=TaskStatus.PENDING),
    ]
    placements, total_secs = project_steps(steps, js, now)
    # elapsed 30s + remaining: running projects to 60, pending adds 60 -> 120
    assert total_secs == 120
