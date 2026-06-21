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

## Implementation Preference: Dogfooding via `mini`

When implementing features or fixes:
1. **Prefer `mini` commands** as the primary method — this dogfoods the tool and validates real-world usage
2. **Only fall back** to direct code changes when:
   - The feature is blocked in `mini` (e.g., WebSocket handlers not yet implemented)
   - The fix requires direct code changes that `mini` cannot invoke (e.g., schema migrations, internal refactors)
   - Direct changes make the dogfooding path clearer (rare)

This keeps the tool dogfood-friendly and ensures CLI/API actually work.

## Current State
- ✅ Backend: Production-ready (42/42 tests)
- ⏳ Next: Phase 4 visualization UIs
