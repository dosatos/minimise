from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"

class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


def _add_counts(a: Optional[int], b: Optional[int]) -> Optional[int]:
    """a + b, where None means not reported: None only if neither side reported."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


@dataclass
class TokenUsage:
    """The tokens one agent call used (an agent_runs row's tokens). None = the
    harness didn't report it.

    The four buckets split the tokens with no overlap: input is the tokens
    neither read from nor written to the cache, and output already includes
    the reasoning tokens. reasoning is the part of output spent reasoning, so
    it is never added to total. reasoning and by_model (model id -> the four
    buckets) are stored, never shown.
    """
    input: Optional[int]
    cache_read: Optional[int]
    cache_write: Optional[int]
    output: Optional[int]
    reasoning: Optional[int] = None
    by_model: Optional[dict] = None

    BUCKETS = ("input", "cache_read", "cache_write", "output")

    @classmethod
    def reported(cls, input: Optional[int], cache_read: Optional[int],
                 cache_write: Optional[int], output: Optional[int],
                 reasoning: Optional[int] = None,
                 by_model: Optional[dict] = None) -> Optional["TokenUsage"]:
        """A TokenUsage, or None when no bucket holds a count: all None or all zero
        (some harnesses emit all-zero usage when they report nothing)."""
        if not any((input, cache_read, cache_write, output)):
            return None
        return cls(input, cache_read, cache_write, output, reasoning, by_model)

    @property
    def total(self) -> int:
        """The sum of the reported buckets; reasoning is never added."""
        return sum(getattr(self, b) for b in self.BUCKETS if getattr(self, b) is not None)

    def __add__(self, other: Optional["TokenUsage"]) -> "TokenUsage":
        """Two runs' tokens together. Adding None (nothing reported) changes nothing."""
        if other is None:
            return self
        if not isinstance(other, TokenUsage):
            return NotImplemented
        by_model = None
        if self.by_model is not None or other.by_model is not None:
            by_model = {}
            for side in (self.by_model or {}, other.by_model or {}):
                for model, counts in side.items():
                    merged = by_model.get(model, {})
                    by_model[model] = {b: _add_counts(merged.get(b), counts.get(b))
                                       for b in self.BUCKETS}
        return TokenUsage(
            *(_add_counts(getattr(self, b), getattr(other, b)) for b in self.BUCKETS),
            reasoning=_add_counts(self.reasoning, other.reasoning),
            by_model=by_model,
        )

    __radd__ = __add__  # None + usage is usage too

    def to_json_dict(self) -> dict:
        """The stored form (agent_runs.tokens). Not the API shape: that never
        carries reasoning or by_model."""
        return {
            "input": self.input,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "output": self.output,
            "reasoning": self.reasoning,
            "by_model": self.by_model,
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> "TokenUsage":
        return cls(
            input=data.get("input"),
            cache_read=data.get("cache_read"),
            cache_write=data.get("cache_write"),
            output=data.get("output"),
            reasoning=data.get("reasoning"),
            by_model=data.get("by_model"),
        )


@dataclass
class AgentRun:
    """One agent call (an agent_runs row): appended when it starts, finished once.

    Job runs carry job_id, task_id and the 0-based attempt (= Execution.attempt);
    loop runs carry loop_id and step_id. ended_at None = it never returned
    (killed, or still running); tokens None = not reported.
    """
    run_id: str
    job_id: Optional[str]
    task_id: Optional[str]
    attempt: Optional[int]
    loop_id: Optional[str]
    step_id: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime] = None
    tokens: Optional[TokenUsage] = None


@dataclass
class Task:
    id: str
    job_id: str
    name: str
    description: str
    estimated_duration_min: int
    status: TaskStatus = TaskStatus.PENDING
    retries: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    diff_path: Optional[str] = None
    base_commit: Optional[str] = None
    goal: Optional[str] = None
    assignee: Optional[str] = None
    harness: Optional[str] = None
    model: Optional[str] = None
    timeout_min: Optional[int] = None  # validated at the plan boundary; None = unbounded

    def to_dict(self) -> dict:
        """Convert Task object to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "retries": self.retries,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "diff_path": self.diff_path,
            "assignee": self.assignee,
            "harness": self.harness,
            "model": self.model,
            "goal": self.goal,
        }

@dataclass
class Execution:
    """One timed run: a task attempt or a plan/per-task hook.

    Identity is the derived ``execution_id`` — opaque, never parsed.
    """
    task_id: Optional[str]  # None for plan-level hooks
    attempt: int
    job_id: str = ""
    execution_type: str = "task"
    status: TaskStatus = TaskStatus.RUNNING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    diff_path: Optional[str] = None
    commit_sha: Optional[str] = None
    hook_name: Optional[str] = None  # set on hooks; NULL for task attempts
    exit_reason: Optional[str] = None  # machine classification of how the run ended

    @property
    def execution_id(self) -> str:
        """Deterministic opaque id. Readable {key}#{value} pairs; a segment is
        present only when meaningful. Never parse it — it's the PK / resume key."""
        parts = [f"job#{self.job_id}"]
        if self.task_id:
            parts.append(f"task#{self.task_id}")
        if self.hook_name:
            parts.append(f"{self.execution_type}_hook#{self.hook_name}")
        else:
            parts.append(f"attempt#{self.attempt}")
        return "#".join(parts)

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "execution_type": self.execution_type,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "diff_path": self.diff_path,
            "commit_sha": self.commit_sha,
            "hook_name": self.hook_name,
            "exit_reason": self.exit_reason,
        }


@dataclass
class Job:
    id: str
    name: str
    status: JobStatus = JobStatus.PENDING
    plan_path: str = ""
    base_commit: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tasks: list[Task] = field(default_factory=list)
    pid: Optional[int] = None
    stop_reason: Optional[str] = None  # why it stopped on its own, e.g. "usage limit: resets 4am"
    tokens_untracked: bool = False  # made before agent_runs existed: no token figures

    def to_dict(self) -> dict:
        """Convert Job object to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "stop_reason": self.stop_reason,
            "plan_path": self.plan_path,
            "base_commit": self.base_commit,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "tasks": [t.to_dict() for t in self.tasks] if self.tasks else [],
        }

def _validate_timeout(model):
    """timeout_min is the hard kill deadline; below the estimate it's a typo."""
    if model.timeout_min is not None and model.timeout_min < model.estimated_duration_min:
        raise ValueError(
            f"timeout_min ({model.timeout_min}) must be >= "
            f"estimated_duration_min ({model.estimated_duration_min})"
        )
    return model


class Hook(BaseModel):
    """A named, timed lifecycle step. Has a shell command -> a shell script;
    no shell -> a bare name resolved from config (deferred). No type, no when."""
    name: str
    estimated_duration_min: int = Field(gt=0, strict=True)
    timeout_min: Optional[int] = Field(default=None, gt=0, strict=True)
    shell: Optional[str] = None
    on_failure: Literal["fail", "retry", "skip"] = "fail"

    @model_validator(mode="after")
    def _check_timeout(self):
        return _validate_timeout(self)


def _validate_hook_list(hooks: list["Hook"], where: str) -> None:
    names = [h.name for h in hooks]
    if len(names) != len(set(names)):
        raise ValueError(f"hook names must be unique within {where}")
    is_task_post = "pre_hooks" not in where and not where.startswith("plan ")
    for h in hooks:
        if h.on_failure != "fail" and not is_task_post:
            raise ValueError(
                f"hook '{h.name}' in {where} has on_failure='{h.on_failure}', "
                "which (any non-default value) is only valid for a task's post_hooks"
            )
        if h.shell is None or not h.shell.strip():
            # Bare name = a config-hook reference; resolver not built yet.
            raise ValueError(
                f"hook '{h.name}' in {where} has no shell command "
                "(named config-hooks are not supported yet)"
            )


class PlanTask(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    description: str
    goal: str
    # strict rejects bool (an int subclass) and float; gt=0 keeps it positive
    estimated_duration_min: int = Field(gt=0, strict=True)
    timeout_min: Optional[int] = Field(default=None, gt=0, strict=True)
    assignee: Optional[str] = None
    harness: Optional[str] = None
    model: Optional[str] = None
    pre_hooks: list[Hook] = Field(default_factory=list)
    post_hooks: list[Hook] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_hooks(self):
        if self.assignee is not None:
            for field in ("harness", "model"):
                if getattr(self, field) is not None:
                    raise ValueError(
                        f"task '{self.id}': '{field}' and 'assignee' are mutually exclusive "
                        "(the persona already carries its own harness/model)"
                    )
        # extra="allow" would silently drop it; Task has no effort column.
        if "effort" in (self.model_extra or {}):
            raise ValueError(
                f"task '{self.id}': 'effort' is not a task field; "
                "set effort on the task's assignee persona instead"
            )
        _validate_hook_list(self.pre_hooks, f"task '{self.id}' pre_hooks")
        _validate_hook_list(self.post_hooks, f"task '{self.id}' post_hooks")
        return _validate_timeout(self)

class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    briefing: Optional[str] = None
    tasks: list[PlanTask] = Field(min_length=1)
    pre_hooks: list[Hook] = Field(default_factory=list)
    post_hooks: list[Hook] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_task_ids(self):
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        _validate_hook_list(self.pre_hooks, "plan pre_hooks")
        _validate_hook_list(self.post_hooks, "plan post_hooks")
        return self

    @classmethod
    def from_yaml(cls, path):
        with open(path) as f:
            raw = yaml.safe_load(f)
        if isinstance(raw, dict):
            raw = raw.get("plan", raw)
        return cls.model_validate(raw)


# Levels `claude --effort` accepts.
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Worker(BaseModel):
    """A step's worker: exactly one of persona|prompt|prompt_file, or none
    (= the built-in default). More than one set is an error.

    harness/model/effort may accompany prompt or prompt_file, but not persona
    (the persona already carries its own harness/model/effort)."""
    persona: Optional[str] = None
    prompt: Optional[str] = None
    prompt_file: Optional[str] = None
    harness: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[Effort] = None

    @model_validator(mode="after")
    def _exactly_one_or_none(self):
        set_fields = [k for k in ("persona", "prompt", "prompt_file")
                      if getattr(self, k) is not None]
        if len(set_fields) > 1:
            raise ValueError(
                f"worker must set at most one of persona|prompt|prompt_file, got {set_fields}"
            )
        if self.persona is not None:
            for field in ("harness", "model", "effort"):
                if getattr(self, field) is not None:
                    raise ValueError(
                        f"worker: '{field}' and 'persona' are mutually exclusive "
                        "(the persona already carries its own harness/model/effort)"
                    )
        return self


class Dimension(Worker):
    name: str
    rubric: str


class EvaluateConfig(BaseModel):
    # A typo here (e.g. "selct") must fail loudly, not silently evaluate everything.
    model_config = ConfigDict(extra="forbid")
    # "failed": skip a dimension whose last pass is still valid (see LoopEngine).
    select: Literal["all", "failed"] = "all"
    max_concurrent: int = Field(gt=0, strict=True, default=4)
    dimensions: list[Dimension] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_dimension_names(self):
        names = [d.name for d in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("evaluate dimension names must be unique")
        return self


class LoopConfig(BaseModel):
    plan: Worker
    implement: Worker
    evaluate: EvaluateConfig


class LoopSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str
    name: str
    goal: str
    loop: LoopConfig
    max_iterations: int = Field(gt=0, strict=True)
    plan_version: int = Field(default=1, ge=1, strict=True)

    @classmethod
    def from_yaml(cls, path):
        with open(path) as f:
            raw = yaml.safe_load(f)
        return cls.model_validate(raw)


@dataclass
class Loop:
    loop_id: str
    name: str
    status: JobStatus = JobStatus.PENDING
    plan_path: str = ""
    max_iterations: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    pid: Optional[int] = None
    stop_reason: Optional[str] = None  # why it stopped on its own, e.g. "usage limit: resets 4am"
    tokens_untracked: bool = False  # made before agent_runs existed: no token figures
    cwd: Optional[str] = None  # where its engine last ran: relative prompt files resolve here


@dataclass
class LoopStep:
    step_id: str
    loop_id: str
    iteration: int
    step_type: str  # 'plan' | 'implement' | 'evaluate'
    dimension: Optional[str] = None  # None except evaluate
    status: TaskStatus = TaskStatus.PENDING
    retries: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
