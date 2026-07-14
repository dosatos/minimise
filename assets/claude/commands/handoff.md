---
description: Create a session handoff document for the next session
argument-hint: [optional summary notes]
---

Create a handoff to summarize this session's work for next time:

1. **Copy template:**
   ```bash
   TIMESTAMP=$(date +%Y-%m-%d-%H%M)
   cp worklogs/handoffs/HANDOFF_TEMPLATE.md "worklogs/handoffs/session-$TIMESTAMP.md"
   ```

2. **Edit the file and fill in:**
   - ✅ What was accomplished (1-3 bullets)
   - 🚀 What's next (immediate, blockers, phase)
   - 🔧 How to run (test/use commands)
   - ⚠️ Gotchas (critical things to remember)
   - 📍 Current state (branch, tests passing, status)

3. **Update symlink:**
   ```bash
   ln -sf "session-$TIMESTAMP.md" worklogs/handoffs/session-latest.md
   ```

**Pro tip:** Use your notes above to fill in the template quickly.

## Carry forward the execution rule

In the handoff's **How to run** / **Gotchas**, remind the next session:

> Delegate implementation to `mini job`, don't edit inline. It keeps the
> orchestrating context clean (execution churn stays in the job) and, run via
> the `/minimise:plan-review` skill, adds the plan-quality gate for free. Direct
> edits only for trivial one-liners or `mini`-blocked changes (migrations,
> internal refactors).

If this session did any implementation directly instead of via `mini job`, note
that as debt so next session can course-correct.

$ARGUMENTS
