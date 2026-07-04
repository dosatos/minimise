from minimise.models import JobStatus, LoopSpec
from minimise.storage.loop_store import LoopStore

SPEC = {
    "version": "1",
    "name": "Refine It",
    "goal": "make the thing better",
    "max_iterations": 5,
    "loop": {
        "plan": {"prompt": "plan the work"},
        "implement": {"prompt": "do the work"},
        "evaluate": {"dimensions": [{"name": "quality", "rubric": "is it good?"}]},
    },
}


def test_create_registers_loop_and_mirrors_spec(db, temp_db_dir):
    """create registers a PENDING loop and reads its spec back faithfully."""
    jobs_dir = temp_db_dir / "jobs"
    store = LoopStore(db, jobs_dir)

    spec = LoopSpec.model_validate(SPEC)
    loop = store.create(spec, plan_path="loop.yaml")

    assert loop.loop_id.startswith("loop-")
    assert (jobs_dir / loop.loop_id / "plan.yaml").exists()

    loaded = store.load(loop.loop_id)
    assert loaded is not None
    assert loaded.name == "Refine It"
    assert loaded.status == JobStatus.PENDING
    assert loaded.max_iterations == 5
    assert loaded.plan_path == "loop.yaml"

    # Mirrored spec round-trips to an equal LoopSpec.
    assert store.load_spec(loop.loop_id) == spec


def test_journal_and_log_paths(db, temp_db_dir):
    """journal_path and loop_log_path are distinct files under the loop dir, parent ensured."""
    jobs_dir = temp_db_dir / "jobs"
    store = LoopStore(db, jobs_dir)

    journal = store.journal_path("loop-abc")
    log = store.loop_log_path("loop-abc")

    assert journal == jobs_dir / "loop-abc" / "journal.jsonl"
    assert log == jobs_dir / "loop-abc" / "job.log"
    assert journal != log
    assert journal.parent.exists() and log.parent.exists()
