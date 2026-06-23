# Design: AgentHarness Interface + ClaudeCodeHarness

**Date:** 2026-06-21
**Status:** Approved (design), pending spec review

## Problem

Two places in `minimise` talk to the Claude Code harness, each with its own
duplicated auth/model/transport logic:

| Caller | Transport today | Purpose |
|---|---|---|
| `TaskExecutor._invoke_claude_code` | `claude -p` CLI subprocess | Agentic — runs an agent that edits the repo |
| `PlanReviewer.review` | Anthropic SDK (`messages.create`) | One-shot — asks a model a question, gets text |

Both independently re-derive `CLAUDE_CODE_USE_BEDROCK`, Bedrock vs Anthropic
credentials, and model selection. This duplication makes maintenance harder,
makes adding another harness painful, and means any new harness use would
re-implement the same plumbing.

## Goals

1. **Maintainability** — all harness interaction lives in one class.
2. **Extensibility** — adding another harness later means one new implementation.
3. **Reuse** — new harness uses (beyond exec/review) need no new plumbing.

## Design

### New module: `src/minimise/harness.py`

A single-method abstract interface and one concrete implementation.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class HarnessResult:
    success: bool
    output: str
    error: str | None = None

class AgentHarness(ABC):
    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        timeout: int = 300,
        model: str | None = None,
        allow_edits: bool = False,
    ) -> HarnessResult:
        """Send a prompt to the harness and return its text output.

        allow_edits=True permits the agent to modify files (adds
        --dangerously-skip-permissions). When False, the call is a
        read-only completion.
        """
```

### Implementation: `ClaudeCodeHarness(AgentHarness)`

Owns everything harness-specific:

- **`_build_env()`** — moved verbatim from `TaskExecutor._build_claude_env`.
  Returns the minimal safe env: common keys (PATH, HOME, USER, SHELL, LANG)
  plus either the Bedrock credential set (`CLAUDE_CODE_USE_BEDROCK`, `AWS_*`,
  `AWS_BEARER_TOKEN_BEDROCK`) or `ANTHROPIC_API_KEY`, based on
  `CLAUDE_CODE_USE_BEDROCK == "1"`.
- **Command construction** — single source of truth:
  - base: `["claude", "-p", "--output-format", "text"]`
  - if `allow_edits`: append `--dangerously-skip-permissions`
  - if `model`: append `--model`, `<model>`
- **`run()`** — `subprocess.run(cmd, input=prompt, capture_output=True,
  text=True, timeout=timeout, cwd=cwd, env=self._build_env())`. Maps:
  - returncode 0 → `HarnessResult(success=True, output=stdout)`
  - returncode != 0 → `HarnessResult(success=False, output=stdout, error=stderr)`
  - `TimeoutExpired` → `HarnessResult(success=False, output="", error="timeout after Ns")`
  - any other exception → `HarnessResult(success=False, output="", error=str(e))`

This preserves the exact `(success, output)` semantics both current callers
rely on; callers adapt to `HarnessResult` at the call site.

### Consumer: `TaskExecutor`

- Constructor gains `harness: AgentHarness | None = None`, defaulting to
  `ClaudeCodeHarness()`. `job_manager.py:39` may pass one explicitly later,
  but the default keeps it zero-config.
- **Delete** `_build_claude_env` (moved to harness).
- `_invoke_claude_code` keeps building the prompt, but replaces the subprocess
  block with:
  ```python
  result = self.harness.run(prompt, cwd=repo_root, allow_edits=True)
  return result.success, (result.output if result.success else (result.error or result.output))
  ```
  (Repo root is the existing `self.jobs_dir.parent.parent`.)

### Consumer: `PlanReviewer`

- **Delete** the entire `Anthropic` / `AnthropicBedrock` constructor branch and
  the `import` from `anthropic`. (`anthropic` is not declared in
  `pyproject.toml` — it is an undeclared ambient import — so no dependency-file
  change is needed; removing the import is sufficient.)
- Constructor gains `harness: AgentHarness | None = None`, defaulting to
  `ClaudeCodeHarness()`. The `api_key` param is dropped (auth is now the
  harness's concern via env passthrough).
- Model selection stays: Bedrock → `BEDROCK_MODEL_ID` or
  `us.anthropic.claude-sonnet-4-6`; otherwise `claude-opus-4-8`. The chosen
  model is passed as `harness.run(prompt, model=review_model, timeout=120)`.
- `review()` calls `harness.run(...)`, then feeds `result.output` into the
  unchanged `_extract_json` / `_parse_response` (fence-stripping + fail-loud).
- **Retry-once on parse failure:** wrap the parse; if `_parse_response` raises
  `ValueError` (unparseable), call `harness.run(...)` one more time and parse
  again. If the second attempt also fails to parse, propagate the `ValueError`
  (preserves the hard-gate). If `result.success` is False on either call,
  raise a `ValueError` describing the harness failure (review cannot be
  verified → must not silently pass).

### CLI

No change required at `cli.py:119` (`PlanReviewer()` still constructs with the
default harness). Behavior is identical from the user's perspective.

## Error Handling

- Harness failures (non-zero exit, timeout, spawn error) surface as
  `HarnessResult(success=False, ...)`.
- `TaskExecutor` treats `success=False` as a failed attempt → existing retry
  loop applies.
- `PlanReviewer` treats `success=False` or repeated unparseable output as a
  hard failure (raises), never a silent pass — preserving the quality gate.

## Testing

### New: `tests/test_harness.py`
- `_build_env`: Bedrock path includes AWS keys + flag, excludes
  `ANTHROPIC_API_KEY`; Anthropic path includes only `ANTHROPIC_API_KEY`.
  Use `@patch.dict(os.environ, {...}, clear=True)` for isolation.
- Command construction: `--dangerously-skip-permissions` present only when
  `allow_edits=True`; `--model X` present only when `model` given.
- Result mapping: returncode 0 / non-zero / `TimeoutExpired` / generic
  exception → correct `HarnessResult`. Subprocess itself mocked
  (`@patch("minimise.harness.subprocess.run")`).

### Rewrite: `tests/test_plan_reviewer.py`
- Replace `@patch('minimise.plan_reviewer.Anthropic')` + env mocking with a
  fake/mock `AgentHarness` injected into `PlanReviewer(harness=fake)`.
- `fake.run` returns canned `HarnessResult` objects (valid JSON, fenced JSON,
  empty findings, unparseable-then-valid for retry, unparseable-twice for
  fail-loud, `success=False` for harness failure).
- Drop the now-irrelevant `test_review_with_no_api_key_raises_error` /
  init-with-key tests (auth no longer lives in the reviewer).

### Update: `tests/test_task_executor.py`
- Any test that patched the subprocess or `_build_claude_env` now injects a
  mock `AgentHarness` instead and asserts on `harness.run` call args
  (prompt content, `allow_edits=True`, `cwd`).

## Out of Scope (YAGNI)

- No new env/config surface beyond what exists (model arg is per-call; existing
  `BEDROCK_MODEL_ID` override retained).
- No second harness implementation now — the interface makes it cheap later.
- No change to prompt content, retry counts (`MAX_RETRIES`), or CLI UX.
