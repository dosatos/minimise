---
description: Start a session by reading the latest handoff for context
argument-hint: [optional task or question]
---

Read the latest project handoff to orient yourself:

```bash
cat worklogs/handoffs/session-latest.md
```

---

## Execution rule: delegate to `mini job`, don't implement inline

For any real implementation work this session, **delegate it to a `mini job`
rather than editing files directly in this conversation.** Reserve direct edits
for trivial one-liners and for `mini`-blocked changes (schema migrations,
internal refactors `mini` can't invoke).

Two reasons this is the default:

1. **Keeps this context clean.** Execution/implementation churn (file dumps,
   test output, agent narration) stays inside the job, not in the orchestrating
   session — so this context stays about *decisions*, not *diffs*.
2. **Quality gates come free.** A job run through the `mini-plan-review` skill
   gets its plan reviewed before execution — the gates are already wired, you
   just have to use the path.

The flow:

```bash
# 1. Write a plan.yaml (scratch plans -> worklogs/scratch/)
# 2. Review it (adds the quality gate):  invoke the mini-plan-review skill
mini job new --plan worklogs/scratch/<plan>.yaml
mini job start <job-id>
mini job status <job-id>          # watch progress
mini job results <job-id>         # logs + diffs when done
```

---

**Context loaded.** $ARGUMENTS

If you'd like to continue with a specific task, just tell me what to work on!
