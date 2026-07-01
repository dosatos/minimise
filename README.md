# Minimise

[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A CLI tool that **guarantees deterministic, high-quality implementation** of multi-agent plans with fresh context per task, built-in quality guardrails, and centralized orchestration of multiple concurrent jobs.

## The Problem

When delegating complex implementation tasks to AI agents (via Claude Code or similar harnesses), you face three key challenges:

1. **❌ Non-deterministic execution** — No guarantee that implementation plans will be completed 100% as specified. Context bloat in long sessions degrades quality.

2. **❌ Context rot** — Long-running sessions accumulate context junk (previous trials, irrelevant history, noise). This degrades agent performance over time.

3. **❌ Scattered job visibility** — Running multiple jobs across terminals makes it impossible to see status, monitor progress, or estimate completion. The human stays in the loop manually babysitting.

## The Solution

Minimise solves this by:

- **Fresh context per task** — Each task runs in an isolated session with only relevant context passed via structured handover
- **Quality guardrails** — Add verification steps (previous result validation, quality gates) to ensure high-quality output
- **Centralized orchestration** — Delegate multiple jobs to background processes and monitor them from one place
- **Deterministic execution** — Structured task sequencing with retry logic guarantees plans complete as written

## Install

Already installed in editable mode:

```bash
pip install -e .
```

## Quick Start

### 1. Define your implementation plan

Create a plan file (`my-plan.yaml`) that describes what needs to be implemented. Each task starts fresh with only the previous task's output as context:

```yaml
plan:
  name: "Implement Feature X"
  briefing: "Build a new API endpoint with tests and documentation"
  
  tasks:
    - id: task-1
      name: "Write tests"
      goal: "Define comprehensive test cases for the new endpoint"
      description: "Create test file with fixtures and test cases"
    
    - id: task-2
      name: "Implement endpoint"
      goal: "Implement the endpoint to pass all tests from task 1"
      description: "Add endpoint handler, validation, and response formatting"
    
    - id: task-3
      name: "Add verification"
      goal: "Verify implementation quality and ensure tests pass"
      description: "Run full test suite, check coverage, and validate behavior"
```

Each task includes a **goal** field that clearly states the task's objective. The agent receives this goal prepended to the description, ensuring alignment on intent. Each task receives **only** the output of the previous task (git diff, completion report) — fresh context prevents degradation.

### 2. Run tests

```bash
pytest tests/ -v
# Expected: 146 passed
```

### 3. Deferred Execution Workflow

The **deferred execution workflow** lets you create jobs, start them when ready, and monitor progress:

#### Create a job (PENDING state)

```bash
mini job new --plan my-plan.yaml
# Output: Job ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

#### View job details

```bash
mini job show a1b2c3d4
# Shows plan structure and tasks
```

#### Start the job (PENDING → RUNNING)

```bash
mini job start a1b2c3d4
# Job spawned in background, execution begins
```

#### Monitor progress

```bash
# List all jobs
mini job list

# Check specific job status
mini job status a1b2c3d4
```

#### Stop a running job (RUNNING → STOPPED)

```bash
mini job stop a1b2c3d4
# Sends SIGTERM to background process
```

#### Resume a failed/stopped job (FAILED/STOPPED → RUNNING)

```bash
mini job resume a1b2c3d4
# Retries execution from last checkpoint
```

#### View results

```bash
# All task output logs
mini job results logs a1b2c3d4

# Specific task output
mini job results logs a1b2c3d4 --task-id task-1

# All git diffs per task
mini job results diff a1b2c3d4

# Specific task diff
mini job results diff a1b2c3d4 --task-id task-2
```

#### Full task context for debugging

```bash
# Show full prompt with handover context for a task
mini job show a1b2c3d4 --task-id task-2
```

## Task Goals

Each task in a plan should include a `goal` field that clearly states the task's objective:

```yaml
tasks:
  - id: task-1
    name: "Setup database"
    goal: "Create PostgreSQL schema with indexes and migrations"
    description: |
      1. Create migration file
      2. Define schema with proper constraints
      3. Add indexes for performance
```

The **goal** is prepended to the agent's prompt, ensuring clarity on intent. This prevents vague task descriptions from causing agent misalignment and makes task completion criteria explicit.

### Goal vs Description

- **Goal**: One-line objective (e.g., "Implement user authentication API")
- **Description**: Implementation details and steps (e.g., "Add Flask routes, hash passwords with bcrypt, implement JWT")

This separation ensures agents understand *what* is needed before tackling *how*.

## Commands

### Job Lifecycle Commands

```bash
mini job new --plan FILE                      # Create job (PENDING state)
mini job show <ID>                            # Show plan structure
mini job show <ID> --task-id <TASK_ID>        # Show full prompt with context for a task
mini job start <ID>                           # Start job (PENDING → RUNNING)
mini job stop <ID>                            # Stop job (RUNNING → STOPPED)
mini job resume <ID>                          # Retry failed job (FAILED/STOPPED → RUNNING)
```

### Status & Monitoring

```bash
mini job list                                 # List all jobs
mini job status <ID>                          # Show job details and task progress
mini job status <ID> --format json            # JSON output for scripting
mini job delete <ID>                          # Delete job and all tasks
```

### Results & Logs

```bash
mini job logs <ID>                            # View the live agent narration (job.log, JSONL)
mini job logs <ID> -f                         # Tail the narration live until the job ends (Ctrl-C to stop)
mini job logs <ID> --query '<insights query>' # Filter/sort/limit/project log lines (see below)
mini job logs <ID> --query '...' --json       # Emit raw matching JSONL records (jq-friendly)
mini job logs <ID> -f --query '...'           # Live tail with filter applied per line (sort/limit ignored)
mini job results logs <ID>                    # View per-task outputs (DB summary)
mini job results logs <ID> --task-id <TASK>   # Filter by task ID
mini job results diff <ID>                    # View all git diffs
mini job results diff <ID> --task-id <TASK>   # Filter by task ID
```

`job.log` is structured JSONL — one JSON object per line
(`timestamp`, `execution_id`, `type`, `level`, `message`). `--query` accepts a
CloudWatch Insights-style string, clauses separated by `|` (any order, all optional):

- `fields a, b` — project columns (`@message` = whole-record JSON; omit = whole record).
- `filter type = "task" and level != "debug"` — ops `=`, `!=`, `like` (substring);
  `and`/`or` evaluated left-to-right, no parentheses.
- `sort @timestamp desc` — `asc` (default) or `desc`.
- `limit 20`.

`@timestamp` maps to `timestamp`. Bad syntax exits 1 with a clear error.

```bash
mini job logs <ID> --query 'fields @timestamp, message | filter type = "task" | sort @timestamp desc | limit 20'
```

### UI & Server

```bash
mini view start                # Launch web UI (Ctrl+C to stop)
```

## Job Lifecycle

Each job progresses through well-defined states:

```
PENDING ──[start]──> RUNNING ──[complete]──> COMPLETED
                        │
                        ├─[stop]──> STOPPED ──[resume]──> RUNNING
                        │
                        └─[error]──> FAILED ──[resume]──> RUNNING
```

### State Transitions

| From | To | Command | Condition |
|------|-----|---------|-----------|
| PENDING | RUNNING | `mini job start <ID>` | Job must be in PENDING state |
| RUNNING | STOPPED | `mini job stop <ID>` | Job must be in RUNNING state |
| STOPPED | RUNNING | `mini job resume <ID>` | Resume from checkpoint |
| FAILED | RUNNING | `mini job resume <ID>` | Retry failed job |
| RUNNING | COMPLETED | (automatic) | All tasks complete successfully |
| RUNNING | FAILED | (automatic) | Task fails after 3 retries |

### Deferred Execution Benefits

- **🎯 Flexible scheduling** — Create jobs anytime, start when ready
- **🔄 Non-blocking** — Jobs run in background, doesn't block terminal
- **⏸️ Stop/Resume** — Control long-running operations mid-execution
- **📊 Centralized visibility** — Monitor multiple jobs from one command
- **🔁 Resilience** — Resume from checkpoints if job fails
- **✅ Fresh context** — Each task starts with clean environment

## Architecture

- **CLI** → **REST API** (Flask + WebSocket)
- **Job Manager** → orchestrates tasks sequentially
- **Task Executor** → runs tasks with retry (3x) & hooks
- **Handover Manager** → passes context between tasks
- **Git Tracker** → validates state, calculates diffs
- **SQLite Database** → persists state to `~/.minimise/`

See [architecture diagram](docs/architecture/minimise-architecture.excalidraw)

### Hooks

A hook is a named, timed step that runs a shell command in your project's
environment. Add `pre_hooks:` / `post_hooks:` lists at the plan level (run
before/after the whole job) or under any task (run before/after that task):

```yaml
tasks:
  - id: build
    name: Build feature
    estimated_duration_min: 25
    post_hooks:
      - name: Run tests
        shell: "pytest -q"
        estimated_duration_min: 3
```

Each hook shows on the Gantt by name with its estimate, and its output is
queryable via `mini job logs --query`. A failed `post_hook` fails the task.

Every hook receives the plan (YAML) on stdin, so a `pre_plan` hook can review the
plan before implementation — e.g. `shell: "claude -p 'review the plan on stdin' | grep -q FAIL && exit 1 || exit 0"`. A nonzero exit aborts the run.

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

✅ **Production-ready backend**
- 358/358 tests passing
- All core components complete
- Full deferred execution workflow implemented
- Tested with Anthropic & Bedrock backends
- Ready for multi-job orchestration

### Core Features
- ✅ Deterministic task sequencing with retry logic (3x)
- ✅ Fresh context per task via structured handover
- ✅ Git-based state validation and diff tracking (per-task commits)
- ✅ Job timing & progress monitoring (accurate task durations)
- ✅ Concurrent job orchestration
- ✅ **Deferred execution workflow** (new/show/start/stop/resume)
- ✅ Job lifecycle management (PENDING → RUNNING → COMPLETED/FAILED/STOPPED)
- ✅ Named, timed pre/post hooks (plan- and task-level) — run in the project venv, render on the Gantt, queryable in the job log
- ✅ Failed plan persistence & recovery (automatic lock release)
- ✅ Results retrieval (logs, diffs, full context)
- ✅ REST API + WebSocket support
- ✅ SQLite persistence

### Next Phase
- Real-time job monitoring dashboard
- Advanced filtering and bulk operations
- Additional output formats (CSV, HTML reports)
