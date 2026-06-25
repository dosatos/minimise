from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

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

@dataclass
class Task:
    id: str
    job_id: str
    name: str
    description: str
    estimated_duration_min: int
    status: TaskStatus = TaskStatus.PENDING
    output: Optional[str] = None
    retries: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    diff_path: Optional[str] = None
    base_commit: Optional[str] = None
    goal: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert Task object to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "output": self.output,
            "retries": self.retries,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "diff_path": self.diff_path,
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
    output: Optional[str] = None
    diff_path: Optional[str] = None
    commit_sha: Optional[str] = None

    @property
    def execution_id(self) -> str:
        """Deterministic opaque id for this logical run. Never parse it."""
        return (
            f"job_id#{self.job_id}#type#{self.execution_type}"
            f"#task#{self.task_id or ''}#attempt#{self.attempt}"
        )

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
            "output": self.output,
            "diff_path": self.diff_path,
            "commit_sha": self.commit_sha,
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

    def to_dict(self) -> dict:
        """Convert Job object to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "plan_path": self.plan_path,
            "base_commit": self.base_commit,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "tasks": [t.to_dict() for t in self.tasks] if self.tasks else [],
        }

class PlanTask(BaseModel):
    model_config = ConfigDict(extra="allow")  # preserve per-task hooks (pre_task_hook, post_task_hook)
    id: str
    name: str
    description: str
    goal: str
    # strict rejects bool (an int subclass) and float; gt=0 keeps it positive
    estimated_duration_min: int = Field(gt=0, strict=True)

class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    tasks: list[PlanTask] = Field(min_length=1)
    pre_plan_hook: str = ""
    post_plan_hook: str = ""

    @model_validator(mode="after")
    def _unique_task_ids(self):
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        return self

    @classmethod
    def from_yaml(cls, path):
        with open(path) as f:
            raw = yaml.safe_load(f)
        if isinstance(raw, dict):
            raw = raw.get("plan", raw)
        return cls.model_validate(raw)
