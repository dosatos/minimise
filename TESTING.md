# Testing Minimise

## Quick Start (5 minutes)

### 1. Run Tests

```bash
# Run all 42 tests
pytest tests/ -v

# Run specific test suite
pytest tests/test_cli.py -v
```

Expected output: `42 passed`

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

# CLI commands
pytest tests/test_cli.py -v
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

# Create job
mini job new --plan examples/example-plan.yaml

# List jobs
mini job list

# Check status (replace with actual job ID)
mini job status <JOB_ID>

# View logs
mini job logs <JOB_ID>

# Stop job
mini job stop <JOB_ID>

# Resume job
mini job resume <JOB_ID>

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
