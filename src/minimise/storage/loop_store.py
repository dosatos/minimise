"""LoopStore — the single owner of loop persistence (SQLite + jobs_dir).

Mirrors JobStore: inject the Database, own the jobs_dir layout, and speak loop
vocabulary. Prefix-matching id resolution lives in the CLI, not here.
"""

import yaml
from pathlib import Path
from typing import Optional

from datetime import datetime

from minimise.models import Loop, LoopSpec, JobStatus
from minimise.storage.database import Database
from minimise.storage.job_store import _pid_alive
from minimise.utils import ensure_directory, new_id


class LoopStore:
    def __init__(self, db: Database, jobs_dir: Path):
        self.db = db
        self.jobs_dir = ensure_directory(jobs_dir)

    def create(self, spec: LoopSpec, plan_path: str) -> Loop:
        """Register a loop and mirror its spec to jobs_dir/<loop_id>/plan.yaml."""
        loop_id = new_id("loop")
        loop = Loop(
            loop_id=loop_id,
            name=spec.name,
            status=JobStatus.PENDING,
            plan_path=plan_path,
            max_iterations=spec.max_iterations,
        )
        self.db.create_loop(loop)

        loop_dir = ensure_directory(self.jobs_dir / loop_id)
        with open(loop_dir / "plan.yaml", "w") as f:
            yaml.dump(spec.model_dump(), f)

        return loop

    def load(self, loop_id: str) -> Optional[Loop]:
        """Load a loop, reconciling a dead-pid RUNNING loop to FAILED (mirrors
        JobStore.load) so `loop start` can resume a crashed foreground run."""
        loop = self.db.get_loop(loop_id)
        if loop and loop.status == JobStatus.RUNNING and not _pid_alive(loop.pid):
            self.db.update_loop_status(loop_id, status=JobStatus.FAILED, completed_at=datetime.utcnow())
            loop.status = JobStatus.FAILED
        return loop

    def load_spec(self, loop_id: str) -> LoopSpec:
        """Re-read the mirrored spec for a loop."""
        return LoopSpec.from_yaml(self.jobs_dir / loop_id / "plan.yaml")

    def journal_path(self, loop_id: str) -> Path:
        """The append-only loop journal (state events — distinct from job.log narration)."""
        return ensure_directory(self.jobs_dir / loop_id) / "journal.jsonl"

    def loop_log_path(self, loop_id: str) -> Path:
        """The one-file-per-loop live narration log (same convention as JobStore.job_log_path)."""
        return ensure_directory(self.jobs_dir / loop_id) / "job.log"


def demo():
    """Self-check: create → load round-trips a loop and reads its spec back faithfully."""
    import tempfile

    spec_dict = {
        "version": "1",
        "name": "Demo Loop",
        "goal": "make it better",
        "max_iterations": 3,
        "loop": {
            "plan": {"prompt": "plan it"},
            "implement": {"prompt": "do it"},
            "evaluate": {"dimensions": [{"name": "quality", "rubric": "is it good?"}]},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        db = Database(tmp / "test.db")
        db.init_db()
        store = LoopStore(db, tmp / "jobs")

        spec = LoopSpec.model_validate(spec_dict)
        loop = store.create(spec, plan_path="demo.yaml")
        assert loop.loop_id.startswith("loop-")

        loaded = store.load(loop.loop_id)
        assert loaded is not None
        assert loaded.name == "Demo Loop"
        assert loaded.max_iterations == 3
        assert loaded.status == JobStatus.PENDING

        assert store.load_spec(loop.loop_id) == spec
        assert store.journal_path(loop.loop_id).name == "journal.jsonl"
        assert store.loop_log_path(loop.loop_id).name == "job.log"
        print("OK")


if __name__ == "__main__":
    demo()
