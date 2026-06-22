from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

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
    status: TaskStatus = TaskStatus.PENDING
    output: Optional[str] = None
    retries: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    diff_path: Optional[str] = None
    base_commit: Optional[str] = None
    goal: Optional[str] = None
    estimated_duration_min: Optional[int] = field(default_factory=lambda: None)

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
