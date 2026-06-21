import pytest
from minimise.handover_manager import HandoverManager
from minimise.models import Task, TaskStatus
import uuid


def test_build_handover_prompt():
    """Test building a handover prompt with task output, diff, and next task."""
    task_output = "Created database schema"
    diff = """diff --git a/schema.sql b/schema.sql
+CREATE TABLE users (id INT PRIMARY KEY);
+CREATE TABLE posts (id INT PRIMARY KEY);"""

    next_task = Task(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        name="Implement API",
        description="Build REST API endpoints",
        status=TaskStatus.PENDING,
    )

    prompt = HandoverManager.build_handover_prompt(task_output, diff, next_task)

    # Assert key elements are present in the prompt
    assert "Previous Task Summary" in prompt
    assert "Created database schema" in prompt
    assert "Files changed: 1" in prompt
    assert "Implement API" in prompt
    assert "Build REST API endpoints" in prompt


def test_build_handover_prompt_counts_multiple_files():
    """Test that file count is calculated correctly for multiple files."""
    task_output = "Modified multiple files"
    diff = """diff --git a/file1.py b/file1.py
+new content 1
diff --git a/file2.py b/file2.py
+new content 2
diff --git a/file3.py b/file3.py
+new content 3"""

    next_task = Task(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        name="Next Task",
        description="Do something next",
        status=TaskStatus.PENDING,
    )

    prompt = HandoverManager.build_handover_prompt(task_output, diff, next_task)

    assert "Files changed: 3" in prompt


def test_build_handover_prompt_counts_lines():
    """Test that added and removed lines are counted correctly."""
    task_output = "Made code changes"
    diff = """diff --git a/file.py b/file.py
-old line 1
-old line 2
+new line 1
+new line 2
+new line 3"""

    next_task = Task(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        name="Next Task",
        description="Continue work",
        status=TaskStatus.PENDING,
    )

    prompt = HandoverManager.build_handover_prompt(task_output, diff, next_task)

    assert "Lines added: 3" in prompt
    assert "Lines removed: 2" in prompt


def test_build_handover_prompt_truncates_large_diff():
    """Test that large diffs are truncated to 2000 characters."""
    task_output = "Made large changes"
    # Create a diff larger than 2000 chars
    large_diff = "diff --git a/file.py b/file.py\n"
    large_diff += "+large line " * 200  # Add many lines to exceed 2000 chars

    next_task = Task(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        name="Next Task",
        description="Continue work",
        status=TaskStatus.PENDING,
    )

    prompt = HandoverManager.build_handover_prompt(task_output, large_diff, next_task)

    # The diff portion should be truncated (with ... appended)
    assert "..." in prompt
    # Check that the prompt is reasonable in size
    assert len(prompt) < len(large_diff) + 1000  # Some overhead for formatting


def test_build_handover_prompt_includes_next_task_context():
    """Test that next task name and description are properly included."""
    task_output = "Output from task"
    diff = "diff --git a/file.py b/file.py\n+content"

    next_task = Task(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        name="Build Authentication System",
        description="Implement OAuth2 with JWT tokens and refresh mechanism",
        status=TaskStatus.PENDING,
    )

    prompt = HandoverManager.build_handover_prompt(task_output, diff, next_task)

    assert "Build Authentication System" in prompt
    assert "Implement OAuth2 with JWT tokens and refresh mechanism" in prompt


def test_build_handover_prompt_excludes_diff_metadata():
    """Test that diff metadata lines (+++ and ---) are not counted as changes."""
    task_output = "Made code changes"
    # Real unified diff format with metadata lines
    diff = """diff --git a/file.py b/file.py
--- a/file.py
+++ b/file.py
-old line 1
-old line 2
+new line 1
+new line 2
+new line 3"""

    next_task = Task(
        id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        name="Next Task",
        description="Continue work",
        status=TaskStatus.PENDING,
    )

    prompt = HandoverManager.build_handover_prompt(task_output, diff, next_task)

    # The +++ and --- metadata lines should NOT be counted
    # Should only count: 3 added lines and 2 removed lines
    assert "Lines added: 3" in prompt
    assert "Lines removed: 2" in prompt
