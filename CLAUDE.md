# Claude Code Project Settings

## Quick Start

**Global commands (work everywhere):**

```
/onboard              # Read latest handoff + get oriented
/handoff              # Create session handoff for next time
```

Or manually:
```bash
cat worklogs/handoffs/session-latest.md
```

## Environment

- **Python:** 3.9+
- **Package:** minimise (pip install -e .)
- **Entry Point:** `mini` command
- **Tests:** `pytest tests/ -v` (42/42 passing)
- **Config:** `~/.minimise/` (auto-created)

## Session Handoff

At end of each session:
```bash
cp worklogs/handoffs/HANDOFF_TEMPLATE.md worklogs/handoffs/session-YYYY-MM-DD-HHmm.md
# Fill in: what was done, what's next, how to run, gotchas, current state
ln -sf session-YYYY-MM-DD-HHmm.md worklogs/handoffs/session-latest.md
```

Next session, read the handoff to get context.

## Key Commands

```bash
# Orient yourself
cat worklogs/handoffs/session-latest.md

# Run tests
pytest tests/ -v

# Try the tool
mini job new --plan examples/example-plan.yaml
mini job list

# View docs
cat README.md
cat TESTING.md
```

## Current State
- ✅ Backend: Production-ready (42/42 tests)
- ⏳ Next: Phase 4 visualization UIs
