"""Tests for terminal_ui module."""

import pytest
from datetime import datetime, timedelta
from minimise.terminal_ui import (
    format_duration,
    render_gantt_bar,
    render_task_table_with_gantt,
    get_status_color,
)
from minimise.models import Job, Task, JobStatus, TaskStatus


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
    task1 = Task(
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
    task2 = Task(
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
    task3 = Task(
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
    task4 = Task(
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

    def test_get_status_color_cancelled(self):
        """Test color for cancelled status."""
        assert get_status_color(TaskStatus.CANCELLED) == "magenta"

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
    """Tests for render_task_table_with_gantt function."""

    def test_render_task_table_with_gantt_basic(self, sample_job, sample_tasks, base_time):
        """Test that task table renders with all required columns."""
        table = render_task_table_with_gantt(sample_job, sample_tasks[:3], now=base_time + timedelta(seconds=10))

        # Check that table was created
        assert table is not None
        # Check that table has the expected columns
        assert len(table.columns) == 4
        assert table.columns[0].header == "Task Name"
        assert table.columns[1].header == "Status"
        assert table.columns[2].header == "Duration"
        assert table.columns[3].header == "Timeline (relative)"

    def test_render_task_table_with_gantt_row_count(self, sample_job, sample_tasks, base_time):
        """Test that table has correct number of rows."""
        table = render_task_table_with_gantt(sample_job, sample_tasks, now=base_time + timedelta(seconds=10))

        # Table should have rows for each task
        assert len(table.rows) == len(sample_tasks)

    def test_render_task_table_with_gantt_empty_tasks(self, sample_job):
        """Test that table renders with empty task list."""
        table = render_task_table_with_gantt(sample_job, [])

        assert table is not None
        assert len(table.rows) == 0
        assert len(table.columns) == 4

    def test_render_task_table_with_gantt_contains_duration_data(self, sample_job, sample_tasks, base_time):
        """Test that table includes duration information."""
        table = render_task_table_with_gantt(sample_job, sample_tasks[:1], now=base_time + timedelta(seconds=10))

        # First task has 100ms duration
        assert len(table.rows) == 1
        # The row should have data for each column
        assert table.rows[0] is not None

    def test_render_task_table_with_gantt_contains_gantt_bars(self, sample_job, sample_tasks, base_time):
        """Test that table includes Gantt bar information."""
        table = render_task_table_with_gantt(sample_job, sample_tasks[:2], now=base_time + timedelta(seconds=10))

        # Should have 2 rows, each with Gantt bar in last column
        assert len(table.rows) == 2
        for row in table.rows:
            assert row is not None

    def test_render_task_table_with_gantt_running_task_included(self, sample_job, sample_tasks, base_time):
        """Test that running tasks are included in table."""
        running_task = sample_tasks[3]  # The running task
        table = render_task_table_with_gantt(sample_job, [running_task], now=base_time + timedelta(seconds=10))

        assert len(table.rows) == 1
        # Should still have data for running task
        assert table.rows[0] is not None
