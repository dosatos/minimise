"""Opt-in integration tests against an authenticated real `codex` CLI."""

import json
import os
import shutil
import subprocess

import pytest

from minimise.agents.harness import CodexHarness


pytestmark = pytest.mark.skipif(
    os.environ.get("MINIMISE_RUN_CODEX_INTEGRATION") != "1"
    or shutil.which("codex") is None,
    reason="set MINIMISE_RUN_CODEX_INTEGRATION=1 with codex installed",
)


@pytest.fixture(scope="module")
def codex_run(tmp_path_factory):
    log_path = tmp_path_factory.mktemp("codex") / "job.log"
    repo_path = tmp_path_factory.mktemp("codex-repo")
    external_dir = tmp_path_factory.mktemp("codex-external")
    external_artifact = external_dir / "codex-handoff.txt"
    subprocess.run(["git", "init", "-q", str(repo_path)], check=True)
    result = CodexHarness().run(
        (
            "Create codex-harness.txt in the current directory containing exactly "
            "'hello from codex' followed by a newline. Also create "
            f"{external_artifact} containing exactly 'external codex artifact' "
            "followed by a newline. Do not modify any other file. "
            "Then reply with exactly: codex edit complete"
        ),
        cwd=str(repo_path),
        timeout=120,
        allow_edits=True,
        log_path=log_path,
        log_fields={"job_id": "j1", "task_id": "t1"},
    )
    assert result.success is True, result.error
    return result, log_path, repo_path, external_artifact


def test_codex_harness_returns_final_agent_message(codex_run):
    result, _, _, _ = codex_run
    assert result.output.strip().lower() == "codex edit complete"
    assert result.exit_reason == "success"


def test_codex_harness_log_is_valid_jsonl(codex_run):
    _, log_path, _, _ = codex_run
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert records
    assert all(record["job_id"] == "j1" for record in records)
    assert all(record["task_id"] == "t1" for record in records)
    assert records[-1]["message"].strip().lower() == "codex edit complete"


def test_codex_harness_can_edit_the_workspace(codex_run):
    _, _, repo_path, _ = codex_run
    assert (repo_path / "codex-harness.txt").read_text() == "hello from codex\n"


def test_codex_harness_can_edit_outside_the_workspace(codex_run):
    _, _, repo_path, external_artifact = codex_run
    assert repo_path not in external_artifact.parents
    assert external_artifact.read_text() == "external codex artifact\n"
