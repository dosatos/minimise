# Testing Minimise

## Quick Start (5 minutes)

### 1. Run Tests

```bash
# Run all 159 tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_cli.py -v
```

Expected output: `159 passed`

### 1a. New Tests for Deferred Execution Workflow

The test suite now includes 52 comprehensive tests for the new workflow commands:

- **5 START command tests** — PENDING → RUNNING state transitions
- **7 STOP command tests** — RUNNING → STOPPED state transitions
- **6 RESUME command tests** — FAILED/STOPPED → RUNNING retries
- **5 RESULTS LOGS tests** — Task output retrieval with filtering
- **4 RESULTS DIFF tests** — Git diff retrieval
- **4 SHOW command tests** — Plan structure and full prompts
- **6 edge case tests** — Prefix matching, state validation, workflows

Total: **52 new tests** covering all deferred execution scenarios

---

### 2. Try the CLI

The package is already installed and ready to use:

```bash
# Show help
mini --help

# Show job commands
mini job --help
```

---

### 3. Create a Test Job

Create a plan file:

```bash
# Use the example plan
cp examples/example-plan.yaml my-plan.yaml
```

Or create a minimal one:

```yaml
plan:
  name: "Test Plan"
  briefing: "A simple test"
  
  tasks:
    - id: task-1
      name: "First Task"
      description: "This is the first task"
    
    - id: task-2
      name: "Second Task"
      description: "This is the second task that gets context from task 1"
```

### 4. Verify Git State

Minimise requires a clean git repository:

```bash
git status
# Should show: "On branch main, nothing to commit, working tree clean"

# If dirty, commit:
git add .
git commit -m "WIP: testing"
```

### 5. Create a Job

```bash
mini job new --plan my-plan.yaml
```

**Output:**
```
✓ Job created successfully
  Job ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
  Name: Test Plan
  Status: pending
  Tasks: 2
```

### 6. View Jobs

```bash
# List all jobs
mini job list

# Get job status
mini job status <JOB_ID>
```

---

## Testing the Deferred Execution Workflow

The new workflow supports **non-blocking job execution** with start/stop/resume lifecycle management.

### 1. Test Job Creation and Status

```bash
# Create a job (stays in PENDING state)
mini job new --plan examples/example-plan.yaml
# Output: Job ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890

# List jobs with progress
mini job list
# Shows: | a1b2c3d4 | Example Plan | pending | 2025-06-21 15:00:00 | 0/5 |

# Get detailed status
mini job status a1b2c3d4
# Output: Shows job details, task progress, Gantt chart
```

### 2. Test Show Command (View Plan Structure)

```bash
# View plan structure and all tasks
mini job show a1b2c3d4
# Output: 
#   Plan: Example Plan
#   Briefing: [plan briefing]
#   Tasks:
#     - task-1: Write tests (PENDING)
#     - task-2: Implement feature (PENDING)
#     ...

# View full prompt for specific task (with handover context)
mini job show a1b2c3d4 --task-id task-2
# Output:
#   Full Prompt for Task
#   Job: Example Plan (a1b2c3d4)
#   Task: Implement feature (task-2)
#   Status: PENDING
#   
#   Task Description: [full description]
#   Handover Context:
#     From previous task: Write tests
#     Previous Task Output: [output from task-1]
#     Git Changes Summary:
#       Files changed: 3
#       Lines added: 45
#       Lines removed: 12
#     Diff Preview: [first 2000 chars]
```

### 3. Test Start Command (PENDING → RUNNING)

```bash
# Start the job (spawns background process)
mini job start a1b2c3d4
# Output:
#   Job started successfully
#   Job ID: a1b2c3d4
#   PID: 12345

# Verify job is running
mini job status a1b2c3d4
# Output: Status: running, Tasks: 0/5 in progress

# Verify only PENDING jobs can start
mini job start a1b2c3d4
# Expected error: "Job must be in PENDING state to start (current: running)"
```

### 4. Test Stop Command (RUNNING → STOPPED)

```bash
# Create and start another job
mini job new --plan examples/example-plan.yaml
# New Job ID: b2c3d4e5

mini job start b2c3d4e5

# Stop the running job (sends SIGTERM)
mini job stop b2c3d4e5
# Output: Job b2c3d4e5 stopped successfully

# Verify job is stopped
mini job status b2c3d4e5
# Output: Status: stopped

# Try to stop a non-running job
mini job stop b2c3d4e5
# Expected error: "Job must be in RUNNING state to stop (current: stopped)"
```

### 5. Test Resume Command (FAILED/STOPPED → RUNNING)

```bash
# Resume a stopped job
mini job resume b2c3d4e5
# Output: Job b2c3d4e5 resumed and completed successfully

# Resume a failed job (simulated by creating a job in FAILED state)
# Verify it retries execution
mini job status b2c3d4e5
# Output: Status should show progress or completion

# Try to resume a job in other states (should show message)
mini job resume a1b2c3d4
# Expected: Message showing current status (e.g., "already running")
```

### 6. Test Results Commands

#### Results Logs - All Tasks

```bash
# After job completes, view all task outputs
mini job results logs a1b2c3d4
# Output:
#   Results Logs
#   Job: Example Plan (a1b2c3d4)
#   
#   [Task 1 Name]
#     ID: task-1
#     Status: completed
#     Created: 2025-06-21 15:00:00
#     Started: 2025-06-21 15:00:05
#     Completed: 2025-06-21 15:02:30
#     Retries: 0
#     
#     Output:
#       [task-1 output lines...]
#   
#   [Task 2 Name]
#     ...
```

#### Results Logs - Filter by Task

```bash
# View specific task output
mini job results logs a1b2c3d4 --task-id task-1
# Output: Shows only task-1 details and output

# Task ID prefix matching works
mini job results logs a1b2c3d4 --task-id task
# Shows all tasks starting with "task"

# Nonexistent task shows error
mini job results logs a1b2c3d4 --task-id task-99
# Expected error: "Task 'task-99' not found"
```

#### Results Diff - All Tasks

```bash
# View git diffs for all tasks
mini job results diff a1b2c3d4
# Output:
#   Results Diffs
#   Job: Example Plan (a1b2c3d4)
#   
#   [Task 1 Name]
#     ID: task-1
#     Diff Path: /path/to/task-1.diff
#     [diff content...]
#   
#   [Task 2 Name]
#     ...
```

#### Results Diff - Filter by Task

```bash
# View diff for specific task
mini job results diff a1b2c3d4 --task-id task-2
# Output: Shows only task-2 diff

# Task without diff is skipped
mini job results diff a1b2c3d4 --task-id task-with-no-diff
# Output shows task header but "No diff available"
```

### 7. Test Full Workflow

```bash
# Complete workflow: create → show → start → monitor → stop → resume → results

# 1. Create job
JOB_ID=$(mini job new --plan examples/example-plan.yaml | grep "Job ID:" | awk '{print $NF}')

# 2. Show plan structure
mini job show $JOB_ID

# 3. View full context for a task
mini job show $JOB_ID --task-id task-1

# 4. Start the job
mini job start $JOB_ID

# 5. Monitor progress (run several times)
for i in {1..3}; do
  mini job status $JOB_ID
  sleep 5
done

# 6. Stop the job (if still running)
mini job stop $JOB_ID

# 7. Resume from checkpoint
mini job resume $JOB_ID

# 8. View results
mini job results logs $JOB_ID
mini job results diff $JOB_ID --task-id task-1
```

### 8. Test JSON Output Format

```bash
# List jobs as JSON
mini job list --format json
# Output:
# [
#   {
#     "id": "a1b2c3d4-...",
#     "name": "Example Plan",
#     "status": "completed",
#     "created_at": "2025-06-21T15:00:00",
#     "started_at": "2025-06-21T15:00:05",
#     "completed_at": "2025-06-21T15:30:00",
#     "tasks": {
#       "total": 5,
#       "completed": 5
#     }
#   }
# ]

# Get job status as JSON
mini job status a1b2c3d4 --format json
# Output:
# {
#   "id": "a1b2c3d4-...",
#   "name": "Example Plan",
#   "status": "completed",
#   "created_at": "2025-06-21T15:00:00",
#   "tasks": [
#     {
#       "id": "task-1",
#       "name": "Write tests",
#       "status": "completed",
#       "started_at": "2025-06-21T15:00:05",
#       "completed_at": "2025-06-21T15:02:30",
#       "duration_seconds": 145
#     },
#     ...
#   ]
# }
```

### 9. Test Error Conditions

```bash
# Try to start a job that doesn't exist
mini job start nonexistent-id
# Expected: "Error: Job 'nonexistent-id' not found"

# Try to show a nonexistent job
mini job show fake-job-id
# Expected: "Error: Job 'fake-job-id' not found"

# Try to show a task in a nonexistent job
mini job show a1b2c3d4 --task-id task-999
# Expected: "Error: Task 'task-999' not found"

# Try to get results for nonexistent job
mini job results logs fake-job-id
# Expected: "Error: Job 'fake-job-id' not found"

# Try to get results for nonexistent task
mini job results logs a1b2c3d4 --task-id task-999
# Expected: "Error: Task 'task-999' not found"
```

### 10. Test Prefix Matching

All commands support prefix matching for job IDs and task IDs:

```bash
# List jobs to see full IDs
mini job list

# Use only first 8 characters instead of full UUID
mini job status a1b2c3d4
mini job start a1b2c3d4
mini job stop a1b2c3d4
mini job resume a1b2c3d4
mini job show a1b2c3d4

# Use even fewer characters if unambiguous
mini job status a1b2c3
mini job stop a1b2

# Multiple matches show error with suggestions
mini job status a  # If multiple jobs start with 'a'
# Expected:
#   Error: Multiple jobs match 'a':
#     a1b2c3d4-...
#     a5f6g7h8-...
#   Please provide more characters to disambiguate
```

---

## Advanced Testing

### Run Tests with Coverage

```bash
pytest tests/ --cov=src/minimise --cov-report=html
open htmlcov/index.html
```

### Run Specific Test Suites

```bash
# Database layer
pytest tests/test_database.py -v

# Git operations
pytest tests/test_git_tracker.py -v

# Task execution with retries
pytest tests/test_task_executor.py -v

# Job orchestration
pytest tests/test_job_manager.py -v

# REST API and WebSocket
pytest tests/test_api_server.py -v

# CLI commands (159 total)
pytest tests/test_cli.py -v
```

### Run Tests for Specific Deferred Execution Commands

```bash
# START command tests (5 tests)
pytest tests/test_cli.py::test_start_pending_job_sets_running_status -v
pytest tests/test_cli.py::test_start_already_running_job_fails -v
pytest tests/test_cli.py::test_start_completed_job_fails -v
pytest tests/test_cli.py -k "test_start" -v

# STOP command tests (7 tests)
pytest tests/test_cli.py -k "test_stop" -v

# RESUME command tests (6 tests)
pytest tests/test_cli.py -k "test_resume" -v

# RESULTS LOGS command tests (5 tests)
pytest tests/test_cli.py -k "test_results_logs" -v

# RESULTS DIFF command tests (4 tests)
pytest tests/test_cli.py -k "test_results_diff" -v

# SHOW command tests (4 tests)
pytest tests/test_cli.py -k "test_show" -v

# All deferred execution workflow tests (52 total)
pytest tests/test_cli.py -k "start or stop or resume or results or show" -v
```

### Test with Verbose Output

```bash
# Show print statements and detailed output
pytest tests/ -v -s

# Show only failed tests with details
pytest tests/ -v --tb=long -x
```

---

## Manual Testing

### Test Database Operations

```python
from minimise.database import Database
from minimise.models import Job, JobStatus
from pathlib import Path

# Initialize database
db = Database(Path.home() / ".minimise" / "test.db")
db.init_db()

# Create a job
job = Job(
    id="test-1",
    name="Test Job",
    status=JobStatus.PENDING,
    plan_path="plan.yaml"
)
db.create_job(job)

# Retrieve job
retrieved = db.get_job("test-1")
print(f"Job: {retrieved.name}, Status: {retrieved.status}")
```

### Test Git Tracking

```python
from minimise.git_tracker import GitTracker
from pathlib import Path

tracker = GitTracker(Path.cwd())

# Check git state
is_clean, msg = tracker.validate_clean_state()
print(f"Git clean: {is_clean}")

# Get current commit
commit = tracker.get_current_commit()
print(f"Current commit: {commit}")

# Get diff (requires commits)
# diff = tracker.get_diff(commit)
# print(diff)
```

### Test Job Manager

```python
from minimise.job_manager import JobManager
from minimise.database import Database
from minimise.git_tracker import GitTracker
from pathlib import Path

db = Database(Path.home() / ".minimise" / "test.db")
db.init_db()
git = GitTracker(Path.cwd())
job_mgr = JobManager(db, git, Path.home() / ".minimise" / "jobs", Path.cwd())

# Create a job from plan
job = job_mgr.create_job(Path("examples/example-plan.yaml"))
print(f"Created job: {job.id}")
print(f"Tasks: {len(job.tasks)}")
```

---

## Testing the REST API

### Start the API Server

```python
from minimise.api_server import APIServer
from minimise.database import Database
from minimise.job_manager import JobManager
from minimise.git_tracker import GitTracker
from pathlib import Path

db = Database(Path.home() / ".minimise" / "test.db")
db.init_db()
git = GitTracker(Path.cwd())
job_mgr = JobManager(db, git, Path.home() / ".minimise" / "jobs", Path.cwd())

api = APIServer(db, job_mgr, port=5000)
api.start()

# Now test in another terminal:
# curl http://localhost:5000/jobs
# curl -X POST http://localhost:5000/jobs -H "Content-Type: application/json" -d '{"plan_path": "examples/example-plan.yaml"}'
```

### Test API Endpoints

```bash
# List jobs
curl http://localhost:5000/jobs

# Get specific job
curl http://localhost:5000/jobs/<JOB_ID>

# Get task details
curl http://localhost:5000/jobs/<JOB_ID>/tasks/<TASK_ID>

# Create new job
curl -X POST http://localhost:5000/jobs \
  -H "Content-Type: application/json" \
  -d '{"plan_path": "examples/example-plan.yaml"}'

# Cancel job
curl -X POST http://localhost:5000/jobs/<JOB_ID>/cancel
```

### Test WebSocket

```bash
# Using wscat (npm install -g wscat)
wscat -c ws://localhost:5000/jobs/<JOB_ID>/stream

# Or in Python:
from socketio import Client
sio = Client()
@sio.on('job_status')
def on_job_status(data):
    print(f"Job update: {data}")

sio.connect('http://localhost:5000')
```

---

## Testing the CLI

### Test All Commands

```bash
# Help
mini --help
mini job --help

# Create job (PENDING state)
JOB_ID=$(mini job new --plan examples/example-plan.yaml | grep "Job ID:" | awk '{print $NF}')

# View plan structure
mini job show $JOB_ID

# View full context for a task
mini job show $JOB_ID --task-id task-1

# Start the job (PENDING → RUNNING)
mini job start $JOB_ID

# List jobs (shows progress)
mini job list

# Check status with progress
mini job status $JOB_ID

# View logs
mini job logs $JOB_ID
mini job results logs $JOB_ID
mini job results logs $JOB_ID --task-id task-1

# View diffs
mini job results diff $JOB_ID
mini job results diff $JOB_ID --task-id task-1

# Stop job (RUNNING → STOPPED)
mini job stop $JOB_ID

# Resume job (STOPPED → RUNNING)
mini job resume $JOB_ID

# Delete job
mini job delete $JOB_ID

# Start web UI
mini view start

# In another terminal:
# Stop web UI
mini view stop
```

### Test Error Handling

```bash
# Plan file not found
mini job new --plan nonexistent.yaml
# Expected: Error message

# Git repository dirty
echo "test" > test.txt
mini job new --plan examples/example-plan.yaml
# Expected: Error message about uncommitted changes
git rm test.txt

# Job not found
mini job status invalid-job-id
# Expected: Error message
```

---

## Integration Testing

### Full Workflow

```bash
# 1. Create a plan
cp examples/example-plan.yaml test-plan.yaml

# 2. Commit current state
git add .
git commit -m "Test: create integration test plan"

# 3. Create job
JOB_ID=$(mini job new --plan test-plan.yaml | grep "Job ID:" | awk '{print $NF}')
echo "Created job: $JOB_ID"

# 4. Check status repeatedly
for i in {1..5}; do
  echo "Check $i:"
  mini job status $JOB_ID
  sleep 2
done

# 5. View logs
mini job logs $JOB_ID

# 6. Cleanup
rm test-plan.yaml
git restore .
```

---

## Performance Testing

### Measure Response Time

```bash
# Time a job creation
time mini job new --plan examples/example-plan.yaml

# Time a status check
time mini job status <JOB_ID>

# Time listing jobs
time mini job list
```

### Test Database Performance

```python
import time
from minimise.database import Database
from minimise.models import Job, JobStatus
from pathlib import Path

db = Database(Path.home() / ".minimise" / "perf-test.db")
db.init_db()

# Create 100 jobs
start = time.time()
for i in range(100):
    job = Job(
        id=f"perf-test-{i}",
        name=f"Job {i}",
        status=JobStatus.COMPLETED,
        plan_path="plan.yaml"
    )
    db.create_job(job)
elapsed = time.time() - start
print(f"Created 100 jobs in {elapsed:.2f}s ({100/elapsed:.0f} ops/sec)")

# List all jobs
start = time.time()
jobs = db.list_jobs()
elapsed = time.time() - start
print(f"Listed {len(jobs)} jobs in {elapsed:.2f}s")
```

---

## Continuous Integration

All tests pass in CI:

```bash
pytest tests/ -v --tb=short
```

Coverage: Currently 90%+ across all modules

---

## Troubleshooting Tests

### Database Lock Issues

```bash
# Reset test database
rm ~/.minimise/minimise.db
rm ~/.minimise/minimise.db.lock 2>/dev/null
```

### Git State Issues

```bash
# Reset git to clean state
git status
git add .
git commit -m "fix: clean state for testing"
```

### Port Already in Use

```bash
# Find and kill process using port 5000
lsof -i :5000
kill -9 <PID>

# Or use a different port in tests
mini view start --port 8080
```

---

## Next Steps

- Run the full test suite: `pytest tests/ -v`
- Try creating a custom plan
- Explore the REST API
- Build a UI client (web dashboard, TUI, etc.)
