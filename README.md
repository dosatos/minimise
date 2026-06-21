# Minimise

A CLI tool for orchestrating multi-agent plan execution with task sequencing, retry logic, and git-based diff tracking.

## Install

Already installed in editable mode:

```bash
pip install -e .
```

## Quick Start

### 1. Create a plan file (`my-plan.yaml`)

```yaml
plan:
  name: "My Plan"
  briefing: "Execute multiple tasks sequentially"
  
  tasks:
    - id: task-1
      name: "First Task"
      description: "What needs to be done"
    
    - id: task-2
      name: "Second Task"
      description: "Continues from task 1's output + git diff"
```

### 2. Run tests

```bash
pytest tests/ -v
# Expected: 42 passed
```

### 3. Create a job

```bash
mini job new --plan my-plan.yaml
```

### 4. Check status

```bash
mini job list
mini job status <JOB_ID>
```

## Commands

```bash
mini job new --plan FILE       # Create job
mini job list                  # List all jobs
mini job status <ID>           # Show job details
mini job stop <ID>             # Cancel job
mini job resume <ID>           # Retry failed job
mini job logs <ID>             # View output
mini view start                # Launch web UI
mini view stop                 # Stop server
```

## Architecture

- **CLI** → **REST API** (Flask + WebSocket)
- **Job Manager** → orchestrates tasks sequentially
- **Task Executor** → runs tasks with retry (3x) & hooks
- **Handover Manager** → passes context between tasks
- **Git Tracker** → validates state, calculates diffs
- **SQLite Database** → persists state to `~/.minimise/`

See [architecture diagram](docs/architecture/minimise-architecture.excalidraw)

## Development

```bash
# Run all tests
pytest tests/ -v

# Run specific suite
pytest tests/test_cli.py -v

# View coverage
pytest tests/ --cov=src/minimise --cov-report=html
```

## Files

```
src/minimise/
├── cli.py              # CLI commands
├── job_manager.py      # Orchestration
├── task_executor.py    # Task execution
├── handover_manager.py # Context passing
├── git_tracker.py      # Git operations
├── api_server.py       # REST API
├── database.py         # SQLite
└── models.py           # Data classes

examples/
└── example-plan.yaml   # Example plan

docs/
└── architecture/       # System diagrams
```

## More Info

- **TESTING.md** — detailed testing guide
- **examples/example-plan.yaml** — full example with 5 tasks

## Status

✅ Production-ready backend
- 42/42 tests passing
- All 7 core components complete
- Ready for UI clients and integrations
