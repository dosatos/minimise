# Followup Plan Status & Execution

**Last Updated:** 2026-06-21 (Session in progress)

## Phase 5 Status (Current)

**Job ID:** `4e7b6f22-163a-4e6a-95d9-7eec7d93dd22`
**Status:** RUNNING - Task 6/6 (Verification)

### Completed Tasks
- ✅ Task 1: `job status --format json` implementation (4m 24s)
- ✅ Task 2: `humanize_duration()` for readable times (1m 12s)
- ✅ Task 3: Gantt bar rendering fix (1m 44s)
- ✅ Task 4: Comprehensive tests (3m 14s)
- ✅ Task 5: README/TESTING.md docs update (3m 1s)
- ⏳ Task 6: End-to-end verification (IN PROGRESS)

### Commits Made
```
9fa59ae feat: allow resume on STOPPED jobs, not just FAILED
a883e41 fix: remove conflicting preexec_fn with start_new_session
633caaf chore: test verification setup changes
206f665 docs: Update README and TESTING.md with deferred execution workflow
```

---

## Followup Plan: Ready to Launch 🚀

**File:** `examples/followup-plan-fixes.yaml`  
**Name:** Fix Plan Execution & Add Goal/Duration Fields  
**Tasks:** 6  
**Est. Duration:** 60-70 minutes  

### Requirements Addressed

#### 1. ✅ Per-Task Commits
**Task ID:** `task-1-fix-commits` (15 min)

**Goal:** Each task commits against its base_commit (not HEAD) to prevent commit stacking.

**Implementation:**
- Tests: `test_task_commits_against_base_commit()`, `test_task_commit_message_format()`, etc.
- Modify: `src/minimise/job_manager.py` → `_complete_task()`
- Capture `base_commit` before task starts
- Create diff: `git diff <base_commit>..HEAD`
- Store in: `task.diff_path`
- Commit message: `"Task <id>: <name>"`

**Why:** Prevents overlapping changes from different tasks being stacked in git history; makes each task's work reviewable independently.

---

#### 2. ✅ Failed Plan Handling
**Task ID:** `task-2-failed-plan-handling` (12 min)

**Goal:** Never delete failed plans; always release locks so jobs can be resumed.

**Implementation:**
- Tests: `test_failed_job_persists_in_db()`, `test_failed_job_plan_lock_released()`, etc.
- Modify: `src/minimise/job_manager.py` → `_job_failed()`
- Set `job.status = FAILED`
- Store error reason in `job.output`
- Call: `release_lock(plan_path)`
- Update: `cli.py` → `resume` command to accept FAILED status

**Why:** Allows safe recovery from failures; no lost context; plan locks don't orphan.

---

#### 3. ✅ Goal Attribute
**Task ID:** `task-3-goal-attribute` (10 min)

**Goal:** Add required 'goal' field to every task so agents receive clear intent.

**Implementation:**
- Tests: `test_plan_load_goal_field()`, `test_plan_goal_prepended_to_prompt()`, etc.
- Update: `src/minimise/models.py` → `Task.goal: Optional[str]`
- Validate: `cli.py` → `_load_plan()` requires goal field
- Prepend to prompt: `"Goal: {task.goal}\n\n{description}"`
- Update all examples to include goal field

**Why:** Structured guidance prevents agent misalignment; goal is explicit, not buried in description.

---

#### 4. ✅ Estimated Duration
**Task ID:** `task-4-estimated-duration` (10 min)

**Goal:** Add required 'estimated_duration_min' field to show completion estimates.

**Implementation:**
- Tests: `test_plan_load_estimated_duration()`, `test_job_status_shows_estimated_remaining()`, etc.
- Update: `src/minimise/models.py` → `Task.estimated_duration_min: int`
- Validate: `cli.py` → `_load_plan()` requires positive integer
- Display: `terminal_ui.py` → Show "Est. completion: HH:MM"
- Update all examples to include estimated_duration_min

**Why:** Users can predict job completion; progress bars become absolute, not relative.

---

#### 5. ✅ Schema Documentation
**Task ID:** `task-5-update-schema-docs` (8 min)

**Goal:** Document new fields and commit logic for plan authors.

**Implementation:**
- Create: `docs/PLAN_SCHEMA.md` with all field definitions
- Update: `README.md` to reference schema
- Update: `TESTING.md` with validation error examples
- Add 2+ complete examples with all new fields

---

#### 6. ✅ Integration Test
**Task ID:** `task-6-integration-test` (12 min)

**Goal:** End-to-end verification that all fixes work together.

**Implementation:**
- Create: `examples/integration-test-plan.yaml` (3 tasks, includes failures)
- Run: `mini job new --plan examples/integration-test-plan.yaml`
- Verify: Goal field in job show, estimated duration in status
- Verify: Task commits against base_commit
- Verify: Failed task persists in DB, lock released
- Resume job and verify recovery
- Run: `pytest tests/ -v` → Expect 120+ tests passing

---

## Auto-Launch System

**Watchdog Script:** `/tmp/launch_followup.sh`  
**Status:** RUNNING (PID: 34415)

### How It Works
1. Monitors Phase 5 job (4e7b6f22) every 15 seconds
2. When Phase 5 completes (or fails), triggers:
   ```bash
   mini job new --plan examples/followup-plan-fixes.yaml
   mini job start <new-id>
   ```
3. Prints new job ID to stdout
4. Exits

### Manual Launch (if watchdog fails)
```bash
mini job new --plan examples/followup-plan-fixes.yaml
# Output: Job ID: <new-id>
mini job start <new-id>
mini job status <new-id>
```

---

## Testing After Completion

### Expected Test Results
- Before: ~100+ tests passing
- After: ~120+ tests passing (+20 new tests from 6 tasks)

```bash
pytest tests/ -v
```

### Key Test Suites Added
- `tests/test_job_manager.py`: Per-task commits, failure handling (8+ tests)
- `tests/test_cli.py`: Goal/duration schema, resume FAILED (10+ tests)
- `tests/test_terminal_ui.py`: Status display with estimates (2+ tests)

---

## Monitoring Commands

```bash
# Check Phase 5 (if still running)
mini job status 4e7b6f22
mini job logs 4e7b6f22

# Check Followup (when it starts)
mini job status <followup-id>
mini job status <followup-id> --format json     # JSON output
mini job logs <followup-id>

# List all jobs
mini job list
```

---

## Git Status After Completion

**Current:** 4 commits ahead of remote
```
9fa59ae feat: allow resume on STOPPED jobs, not just FAILED
a883e41 fix: remove conflicting preexec_fn with start_new_session
633caaf chore: test verification setup changes
206f665 docs: Update README and TESTING.md with deferred execution workflow
```

**After Phase 5:** +1 commit (final verification)
**After Followup:** +6 commits (one per task, following existing pattern)

**Then:** `git push` to remote

---

## Architecture Notes

### Dogfooding Principle
All implementation uses `mini job` commands (not direct code edits):
1. Write tests
2. Implement in production code
3. Verify with `mini job status`

This validates the tool works for its intended use case.

### Schema Evolution
- **Task model:** Add fields, keep backward compat
- **Plan YAML:** Make goal + estimated_duration_min required
- **Validation:** Clear error messages with remediation hints

### Error Handling
- Failed plans: Persist, never delete, release locks
- Resume logic: Accept FAILED/STOPPED/PENDING states
- Lock management: Always release on job end

---

## Files Modified/Created

### Phase 5 (Completed)
- `src/minimise/cli.py` — JSON format, resume STOPPED
- `src/minimise/terminal_ui.py` — humanize_duration(), Gantt fix
- `tests/test_cli.py` — 10+ new tests
- `tests/test_terminal_ui.py` — 5+ new tests
- `README.md` — Deferred execution workflow docs
- `TESTING.md` — New test patterns

### Followup (To Be Done)
- `examples/followup-plan-fixes.yaml` — NEW (ready)
- `src/minimise/job_manager.py` — Per-task commits, failure handling
- `src/minimise/models.py` — Add goal, estimated_duration_min fields
- `src/minimise/cli.py` — Schema validation for new fields
- `src/minimise/terminal_ui.py` — Display estimated completion
- `docs/PLAN_SCHEMA.md` — NEW schema documentation
- `examples/integration-test-plan.yaml` — NEW integration test
- `tests/test_job_manager.py` — +8 new tests
- `tests/test_cli.py` — +10 new tests
- `README.md` — Add schema reference
- `TESTING.md` — Add validation examples

---

## Gotchas & Notes

1. **Phase 5 might take 5-10 more minutes** — Task 6 is comprehensive verification
2. **Watchdog timeout:** Max ~15 min wait, then manual launch needed
3. **Per-task commits:** Requires capturing `base_commit` at task start (not at plan start)
4. **Failed job resume:** Update `resume_from_task` to skip failed task by default
5. **Goal field required:** All existing example plans need goal field added
6. **Duration estimates:** Should be realistic; used for progress display accuracy

---

## Success Criteria

✅ All 6 tasks completed  
✅ 120+ tests passing (up from ~100)  
✅ Per-task commits working  
✅ Failed plans persist + can be resumed  
✅ Goal field required + displayed  
✅ Estimated duration displayed  
✅ Schema docs complete  
✅ Integration test passes  
✅ All commits pushed to remote  

---

## Next Steps

1. **Wait for Phase 5** to complete (~5 min)
2. **Followup auto-launches** via watchdog
3. **Monitor execution** with `mini job status <id>`
4. **After completion:** `git push` all commits
5. **Update this doc** with final results

---

**Session Handoff:** See `worklogs/handoffs/NEXT_SESSION.md`
