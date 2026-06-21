# Task 4 Critical Fix Report

## Status: DONE

Both critical database state invariant violations have been fixed and tested.

## Issues Fixed

### Issue #1: Post-Hook Failure State Loss (Lines 81-85)
**Problem:** When post-hook fails after successful task execution, task status was not updated in database. Task remained in RUNNING state despite function returning False.

**Fix Applied:** Added database status update to FAILED before returning from post-hook failure.
```python
if post_task_hook:
    hook_success, hook_output = run_shell_command(post_task_hook)
    if not hook_success:
        # NOW: Update status to FAILED before returning
        self.db.update_task_status(
            task.id,
            TaskStatus.FAILED,
            output=f"Post-task hook failed: {hook_output}",
            retries=task.retries,
            completed_at=datetime.utcnow(),
        )
        return False, f"Post-task hook failed: {hook_output}"
```

### Issue #2: Silent Completion Gap (Lines 87-106)
**Problem:** When task succeeds but base_commit is None, no status update occurred. Successful completion was invisible in database.

**Fix Applied:** Made diff generation conditional (only if base_commit exists) and ensured status ALWAYS updates for successful tasks.
```python
if final_success:
    # NOW: Only get diff if base_commit exists
    if job.base_commit:
        diff = self.git_tracker.get_diff(job.base_commit)
        diff_path = task_dir / "diff.txt"
        diff_path.write_text(diff)
    # ALWAYS update status when task succeeds
    self.db.update_task_status(...)
```

## Commits

- **Hash:** 52f7dfc46797827a9119905c64030ca61f63e972
- **Message:** fix: database state invariant violations in task executor

## Test Results

All tests passing: **4/4** (1 existing + 3 new regression tests)

### Test Breakdown
1. ✅ `test_task_executor_initialization` - Existing (still passing)
2. ✅ `test_pre_post_hooks_execution` - Existing (still passing)
3. ✅ `test_post_hook_failure_updates_status` - NEW: Regression test for Issue #1
4. ✅ `test_task_completion_without_base_commit` - NEW: Regression test for Issue #2

### Test Execution Output
```
tests/test_task_executor.py::test_task_executor_initialization PASSED    [ 25%]
tests/test_task_executor.py::test_pre_post_hooks_execution PASSED        [ 50%]
tests/test_task_executor.py::test_post_hook_failure_updates_status PASSED [ 75%]
tests/test_task_executor.py::test_task_completion_without_base_commit PASSED [100%]

============================== 4 passed in 0.34s =======================================
```

## Files Modified

- **src/minimise/task_executor.py** - Fixed lines 81-106 (post-hook failure + completion logic)
- **tests/test_task_executor.py** - Added 2 new regression tests

## Verification

Both database state invariant violations are now fixed:
- Post-hook failures immediately update task status to FAILED
- Task completion always updates status (COMPLETED or FAILED), regardless of base_commit presence
- All existing tests continue to pass
- New regression tests prevent future reintroduction of these bugs
