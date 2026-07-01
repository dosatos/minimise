# Plan Review Becomes a Hook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Complete — implemented on branch `plan-review-as-hook` (commits 0ae7135, 303e614, 7ad88a7, 83e0303).

**Goal:** Remove the hardcoded plan-review gate and let any `pre_plan` hook review the plan, by feeding the plan YAML to every hook on stdin.

**Architecture:** Delete `PlanReviewer` and the `--skip-review` flag; `mini job new` no longer reviews. Review becomes an ordinary opt-in `pre_plan` hook (the user's `shell:` command reads the plan on stdin and sets the exit code). The only new plumbing: `HookExecutor` pipes the plan YAML to each hook's stdin, threaded down from `JobExecutor.execute` which already holds the `Plan`.

**Tech Stack:** Python 3.9+, Click, Pydantic, PyYAML, pytest.

## Global Constraints

- Tests run with `PYTHONPATH=src pytest tests/ -q` (there is a stale editable install; always set `PYTHONPATH=src`).
- No co-author / "Generated with" trailers on commits.
- Pre-customer: breaking changes are fine, no backfill needed.
- The hook field is `shell:` (not `command:`). A bare-name hook with no `shell` is rejected by validation.
- YAGNI / TDD / frequent commits. This work is mostly deletion.

---

### Task 1: `run_shell_command` accepts stdin

Give the shared shell runner an optional `input` string that is written to the child process's stdin. Nothing else changes; default `None` preserves today's behavior.

**Files:**
- Modify: `src/minimise/utils.py:13-34`
- Test: `tests/test_utils.py` (create if absent)

**Interfaces:**
- Produces: `run_shell_command(command, cwd=None, timeout=3600, env=None, stdin=None) -> tuple[bool, str]` — when `stdin` is a string it is passed to the process on stdin.

- [x] **Step 1: Write the failing test**

Add to `tests/test_utils.py` (create the file if it does not exist, with `from minimise.utils import run_shell_command` at top):

```python
def test_run_shell_command_pipes_stdin():
    ok, out = run_shell_command("cat", stdin="hello-from-stdin")
    assert ok is True
    assert "hello-from-stdin" in out


def test_run_shell_command_stdin_defaults_none():
    # No stdin given: command that reads stdin sees EOF immediately, still succeeds.
    ok, out = run_shell_command("cat", stdin=None)
    assert ok is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_utils.py -q`
Expected: FAIL — `run_shell_command() got an unexpected keyword argument 'stdin'`.

- [x] **Step 3: Add the `stdin` parameter**

In `src/minimise/utils.py`, change the signature and the `subprocess.run` call:

```python
def run_shell_command(command: str, cwd: Optional[Path] = None, timeout: int = 3600,
                      env: Optional[dict] = None, stdin: Optional[str] = None) -> tuple[bool, str]:
    """Execute a shell command; return (success, combined stdout+stderr).

    env=None inherits the current process environment (today's behavior);
    pass a dict to run with a replaced environment (e.g. the target repo's venv).
    stdin, when a string, is written to the child process's stdin.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=stdin,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_utils.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/minimise/utils.py tests/test_utils.py
git commit -m "feat: run_shell_command accepts stdin input"
```

---

### Task 2: HookExecutor pipes the plan YAML to each hook's stdin

Every hook receives the plan (raw YAML) on stdin so a review command has something to read. The plan is serialized once in `JobExecutor.execute` and threaded through `_run_hooks` into `HookExecutor.run`.

**Files:**
- Modify: `src/minimise/orchestration/hook_executor.py:26-50`
- Modify: `src/minimise/orchestration/job_executor.py:21-58`
- Test: `tests/test_hook_executor.py`

**Interfaces:**
- Consumes: `run_shell_command(..., stdin=...)` from Task 1.
- Produces: `HookExecutor.run(hook, execution_type, task_id, stdin=None) -> bool` — passes `stdin` to `run_shell_command`. `JobExecutor._run_hooks(hooks, execution_type, task_id, stdin)` forwards `stdin` to each `run` call.

- [x] **Step 1: Write the failing test**

Add to `tests/test_hook_executor.py`:

```python
def test_run_pipes_stdin_to_hook(tmp_path):
    h = Hook(name="readplan", shell="cat", estimated_duration_min=1)
    ex = _captured_execution_with_stdin(HookExecutor(repo_root=tmp_path), h, "PLAN-YAML-HERE")
    assert "PLAN-YAML-HERE" in ex.output


def _captured_execution_with_stdin(executor, hook, stdin):
    captured = []
    executor.store = type("S", (), {"save_execution": lambda self, e: captured.append(e)})()
    executor.run(hook, "pre_plan", task_id=None, stdin=stdin)
    return captured[0]
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_hook_executor.py::test_run_pipes_stdin_to_hook -q`
Expected: FAIL — `run() got an unexpected keyword argument 'stdin'`.

- [x] **Step 3: Thread stdin through HookExecutor.run**

In `src/minimise/orchestration/hook_executor.py`, change `run` to accept and forward `stdin`:

```python
    def run(self, hook: Hook, execution_type: str, task_id: Optional[str],
            stdin: Optional[str] = None) -> bool:
        """Run one hook in the project env; record + log; return success."""
        started_at = datetime.utcnow()
        env = project_env(self.repo_root) if self.repo_root else None
        success, output = run_shell_command(hook.shell, cwd=self.repo_root, env=env, stdin=stdin)
        completed_at = datetime.utcnow()
```

(The rest of `run` is unchanged.)

- [x] **Step 4: Run the new test to verify it passes**

Run: `PYTHONPATH=src pytest tests/test_hook_executor.py::test_run_pipes_stdin_to_hook -q`
Expected: PASS.

- [x] **Step 5: Serialize the plan and thread it through JobExecutor**

In `src/minimise/orchestration/job_executor.py`, import yaml at the top (after the existing imports):

```python
import yaml
```

Change `_run_hooks` to accept `stdin` and forward it, and have `execute` serialize the plan once and pass it to every `_run_hooks` call:

```python
    def _run_hooks(self, hooks, execution_type, task_id, stdin=None) -> bool:
        for hook in hooks:
            if not self.hook_executor.run(hook, execution_type, task_id, stdin=stdin):
                print(f"{execution_type} hook '{hook.name}' failed")
                return False
        return True

    def execute(self, job: Job, plan: Plan) -> bool:
        """Run all of a job's tasks (and plan hooks); returns True on success."""
        plan_yaml = yaml.dump(plan.model_dump())

        if not self._run_hooks(plan.pre_hooks, "pre_plan", None, stdin=plan_yaml):
            return False

        handover = ""
        for idx, task in enumerate(job.tasks):
            plan_task = plan.tasks[idx] if idx < len(plan.tasks) else None
            next_task = job.tasks[idx + 1] if idx < len(job.tasks) - 1 else None
            pre = getattr(plan_task, "pre_hooks", []) if plan_task else []
            post = getattr(plan_task, "post_hooks", []) if plan_task else []

            if not self._run_hooks(pre, "pre_task", task.id, stdin=plan_yaml):
                self.task_executor.store.mark_task_failed(task, "Pre-task hook failed")
                return False

            success, output = self.task_executor.execute_task(
                task, job.id, handover, next_task=next_task,
            )
            if not success:
                print(f"Task {task.name} failed: {output}")
                return False

            if not self._run_hooks(post, "post_task", task.id, stdin=plan_yaml):
                self.task_executor.store.mark_task_failed(task, "Post-task hook failed")
                return False

            # execute_task returns the completed task's handoff for the next one.
            handover = output

        return self._run_hooks(plan.post_hooks, "post_plan", None, stdin=plan_yaml)
```

- [x] **Step 6: Update the HookExecutor demo (keeps the self-check runnable)**

In `src/minimise/orchestration/hook_executor.py`, the existing `demo()` still passes (stdin defaults to None). No change needed — verify by running it:

Run: `PYTHONPATH=src python -m minimise.orchestration.hook_executor`
Expected: prints `OK`.

- [x] **Step 7: Run the hook + executor suites**

Run: `PYTHONPATH=src pytest tests/test_hook_executor.py tests/test_job_executor.py -q`
Expected: PASS (all existing tests still green; existing `run(...)` calls omit `stdin` and default to None).

- [x] **Step 8: Commit**

```bash
git add src/minimise/orchestration/hook_executor.py src/minimise/orchestration/job_executor.py tests/test_hook_executor.py
git commit -m "feat: pipe plan YAML to hook stdin so pre_plan hooks can review"
```

---

### Task 3: Remove the review gate from `mini job new`

Drop the `--skip-review` flag and the `PlanReviewer` call. `mini job new` validates syntax, then creates the job. Fix the two `test_cli.py` tests that pass `--skip-review`.

**Files:**
- Modify: `src/minimise/interfaces/cli/job.py:45-99`
- Modify: `tests/test_cli.py:1785`, `tests/test_cli.py:1808`
- Test: `tests/test_plan_review_cli.py` (syntax tests still apply; review tests are removed in Task 4)

**Interfaces:**
- Produces: `job_new(plan: str)` — no `skip_review` parameter; no review step.

- [x] **Step 1: Update the two `--skip-review` call sites in `test_cli.py` to fail first**

In `tests/test_cli.py`, remove the `"--skip-review"` argument from both invocations (lines ~1785 and ~1808):

```python
        result = runner.invoke(mini, ["job", "new", "--plan", str(plan_path)])
```

- [x] **Step 2: Run those tests to verify they now fail**

Run: `PYTHONPATH=src pytest tests/test_cli.py -q -k "goal"`
Expected: FAIL — the CLI still tries to run the real `PlanReviewer` (network/harness), so job creation does not reach exit 0 cleanly. (This confirms the gate is still wired.)

- [x] **Step 3: Remove the flag and the review block from `job_new`**

In `src/minimise/interfaces/cli/job.py`, replace the command signature and the review block. The new `job_new` (from the decorator through job creation):

```python
@job.command(name="new")
@click.option("--plan", required=True, help="Path to plan.yaml file")
def job_new(plan: str):
    """Create a new job from a plan file (does not execute)."""
    try:
        plan_path = Path(plan).resolve()

        if not plan_path.exists():
            console.print(f"[red]Error: Plan file not found at {plan_path}[/red]")
            raise SystemExit(1)

        # 1. Load and validate plan syntax
        try:
            plan_obj = Plan.from_yaml(plan_path)
        except pydantic.ValidationError as e:
            console.print("[red]Syntax validation failed:[/red]")
            for i, err in enumerate(e.errors(), 1):
                loc = ".".join(str(p) for p in err["loc"])
                console.print(f"  {i}. {loc}: {err['msg']}")
            raise SystemExit(1)

        console.print("[green]✓[/green] Plan syntax valid")

        # 2. Create the job
        db = get_db()
        job_controller = get_job_controller(db)

        job_obj = job_controller.create_job(plan_path)
```

Delete the entire `# 2. Run agent-based review (unless skipped)` block (the `if not skip_review:` section through its `console.print("[green]✓[/green] Plan review passed")`). Keep everything from `job_obj = job_controller.create_job(plan_path)` onward exactly as-is.

Note: `plan_obj` is still assigned by the syntax-validation step; it is not otherwise used after removing the review, which is fine — the validation side effect is the point.

- [x] **Step 4: Run the CLI suite to verify the goal tests pass**

Run: `PYTHONPATH=src pytest tests/test_cli.py -q -k "goal"`
Expected: PASS (job now created without any review call).

- [x] **Step 5: Commit**

```bash
git add src/minimise/interfaces/cli/job.py tests/test_cli.py
git commit -m "feat: drop --skip-review flag and PlanReviewer gate from job new"
```

---

### Task 4: Delete `PlanReviewer` and its tests; update docs

Remove the dead reviewer module, its re-export, and its tests. Rewrite `test_plan_review_cli.py` to keep only the still-valid syntax-validation tests. Update the README hooks section to mention the plan is available on stdin.

**Files:**
- Delete: `src/minimise/agents/plan_reviewer.py`
- Delete: `tests/test_plan_reviewer.py`
- Modify: `src/minimise/interfaces/cli/__init__.py:35-36`
- Modify: `src/minimise/interfaces/cli/job.py:13` (comment mentioning PlanReviewer)
- Modify: `tests/test_plan_review_cli.py` (drop the `TestPlanReviewCLI` class)
- Modify: `README.md:268-286`

**Interfaces:**
- Consumes: nothing new.
- Produces: `minimise.interfaces.cli` no longer exports `PlanReviewer`.

- [x] **Step 1: Delete the reviewer module and its unit tests**

```bash
git rm src/minimise/agents/plan_reviewer.py tests/test_plan_reviewer.py
```

- [x] **Step 2: Remove the re-export and stale comment**

In `src/minimise/interfaces/cli/__init__.py`, delete these two lines (35-36):

```python
# PlanReviewer re-exported so tests can patch `minimise.interfaces.cli.PlanReviewer`.
from minimise.agents.plan_reviewer import PlanReviewer  # noqa: E402,F401
```

In `src/minimise/interfaces/cli/job.py:13`, simplify the comment (drop the PlanReviewer mention):

```python
import minimise.interfaces.cli as _cli  # patchable constants; read at call time
```

- [x] **Step 3: Drop the review tests from `test_plan_review_cli.py`**

In `tests/test_plan_review_cli.py`, delete the entire `class TestPlanReviewCLI:` (lines 82-153) and the now-unused imports `from unittest.mock import patch, MagicMock`. Keep `class TestPlanValidationCLI` (syntax tests) intact.

- [x] **Step 4: Update the README hooks section**

In `README.md`, append a sentence to the Hooks section (after the paragraph ending "A failed `post_hook` fails the task."):

```markdown
Every hook receives the plan (YAML) on stdin, so a `pre_plan` hook can review the
plan before implementation — e.g. `shell: "claude -p 'review the plan on stdin' | grep -q FAIL && exit 1 || exit 0"`. A nonzero exit aborts the run.
```

- [x] **Step 5: Verify no dangling references remain**

Run: `grep -rn "PlanReviewer\|skip_review\|skip-review\|plan_reviewer\|PLAN_REVIEW_TIMEOUT" src/ tests/ README.md`
Expected: only `src/minimise.egg-info/SOURCES.txt` may still list the old test path (generated file — ignore); optionally the comment in `tests/test_harness.py:240` which just names PlanReviewer as an example — update it to say "e.g. a review hook" or leave it (harmless). No references in `src/minimise/` runtime code, `job.py`, or `cli/__init__.py`.

- [x] **Step 6: Run the full suite**

Run: `PYTHONPATH=src pytest tests/ -q`
Expected: PASS. (Was 358 before; expect fewer as the reviewer tests are removed — no failures, no errors.)

- [x] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete PlanReviewer; review is now a pre_plan hook (docs updated)"
```

---

## Self-Review

- **Spec coverage:** (1) delete gate + flag → Task 3; (2) delete `plan_reviewer.py` + re-export → Task 4; (3) pipe plan YAML on stdin → Tasks 1–2. `mini job new` always creates the job → Task 3. User brings own review command, framework ships nothing → covered (no "ship command" task). Known debt (verdict→exit in user's shell) → documented in README (Task 4 Step 4).
- **Placeholder scan:** none — every code step shows full code.
- **Type consistency:** `run_shell_command(..., stdin=None)` (Task 1) is consumed by `HookExecutor.run(..., stdin=None)` (Task 2), forwarded by `_run_hooks(..., stdin=None)` and `execute`'s `plan_yaml` (Task 2). `job_new(plan: str)` signature matches its Click options (Task 3). Consistent throughout.
