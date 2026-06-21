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
      description: "Define test cases for the new endpoint"
    
    - id: task-2
      name: "Implement endpoint"
      description: "Implement the endpoint to pass tests from task 1"
    
    - id: task-3
      name: "Add verification"
      description: "Verify implementation and run full test suite"
```

Each task receives **only** the output of the previous task (git diff, completion report) — fresh context prevents degradation.

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

✅ **Production-ready backend**
- 80/80 tests passing
- All core components complete
- Tested with Anthropic & Bedrock backends
- Ready for multi-job orchestration

### Core Features
- ✅ Deterministic task sequencing with retry logic (3x)
- ✅ Fresh context per task via structured handover
- ✅ Git-based state validation and diff tracking
- ✅ Job timing & progress monitoring
- ✅ Concurrent job orchestration
- ✅ REST API + WebSocket support
- ✅ SQLite persistence

### Next Phase
- Real-time job monitoring dashboard
- Advanced filtering and bulk operations
- Additional output formats (CSV, HTML reports)
