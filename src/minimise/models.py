from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

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

class PlanTask(BaseModel):
    id: str
    name: str
    description: str
    goal: str
    estimated_duration_min: PositiveInt

class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    tasks: list[PlanTask] = Field(min_length=1)

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
