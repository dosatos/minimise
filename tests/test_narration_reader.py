"""task_narration: join job.log records by step, output precedence, missing log."""

import json
from types import SimpleNamespace

from minimise.interfaces.cli._shared import task_narration


def _task(name, output=""):
    return SimpleNamespace(name=name, output=output)


def _write_log(tmp_path, monkeypatch, job_id="job1"):
    jobs_dir = tmp_path / "jobs"
    (jobs_dir / job_id).mkdir(parents=True)
    log = jobs_dir / job_id / "job.log"
    records = [
        {"type": "task", "step": "Alpha", "message": "alpha line 1"},
        {"type": "task", "step": "Alpha  · try 2", "message": "alpha line 2"},
        {"type": "task", "step": "Beta", "message": "beta line"},
        {"type": "hook", "step": "Alpha", "message": "not a task"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    monkeypatch.setattr("minimise.interfaces.cli.JOBS_DIR", jobs_dir)
    return job_id


def test_joins_and_attributes_retries(tmp_path, monkeypatch):
    job_id = _write_log(tmp_path, monkeypatch)
    # Both the base "Alpha" record and the "Alpha  · try 2" retry are attributed.
    assert task_narration(job_id, _task("Alpha")) == "alpha line 1\nalpha line 2"
    assert task_narration(job_id, _task("Beta")) == "beta line"


def test_output_takes_precedence_and_skips_log(tmp_path, monkeypatch):
    job_id = _write_log(tmp_path, monkeypatch)
    # A failed task's output wins; log is not consulted (would be "alpha line 1\n...").
    assert task_narration(job_id, _task("Alpha", output="it failed")) == "it failed"


def test_missing_log_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("minimise.interfaces.cli.JOBS_DIR", tmp_path / "jobs")
    assert task_narration("nope", _task("Alpha")) == ""
