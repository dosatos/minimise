"""Integration tests exercising PiHarness against the real `pi` binary.

Skipped automatically when `pi` isn't installed/working, so CI environments
without it still pass the rest of the suite.
"""

import json
import subprocess

import pytest

from minimise.agents.harness import PiHarness, _extract_text_pi_final, _extract_text_pi_live

# deepseek-v4-flash reliably emits thinking_delta events, which is what the
# JSON-parsing tests below need to see (the default model may not think at all).
DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash"


def _pi_available() -> bool:
    try:
        proc = subprocess.run(
            ["pi", "--version"], capture_output=True, timeout=10, text=True
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


pytestmark = pytest.mark.skipif(not _pi_available(), reason="pi binary not available")


def _run_pi_raw(prompt: str, model: str, timeout: int = 40) -> list[dict]:
    """Invoke pi directly (bypassing PiHarness) and return parsed JSON-line events."""
    cmd = [
        "pi", "--mode", "json", "-p", "--no-session",
        "--no-extensions", "--no-skills", "--no-context-files",
        "--tools", "read,grep,find,ls", "--model", model,
    ]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def deepseek_events():
    """Raw pi JSON events for a prompt that reliably triggers thinking output.

    Skips dependent tests (rather than failing) when deepseek auth/model
    access isn't available in this environment.
    """
    try:
        events = _run_pi_raw("What is 2+2? Think briefly then answer.", DEEPSEEK_MODEL)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("deepseek model unavailable")
    has_thinking = any(
        e.get("assistantMessageEvent", {}).get("type") == "thinking_delta" for e in events
    )
    if not has_thinking:
        pytest.skip("deepseek run produced no thinking_delta events (auth/model unavailable?)")
    return events


def test_pi_harness_run_says_hello():
    result = PiHarness().run("say hello", timeout=30)
    assert result.success is True
    assert len(result.output) > 0
    assert "hello" in result.output.lower()
    assert result.exit_reason == "success"


def test_pi_harness_run_read_only_still_produces_output():
    result = PiHarness().run("say hello", timeout=30, allow_edits=False)
    assert result.success is True
    assert len(result.output) > 0


def test_pi_harness_run_returns_harness_result_fields():
    result = PiHarness().run("say hello", timeout=30)
    assert isinstance(result.success, bool)
    assert isinstance(result.output, str)
    assert result.error is None or isinstance(result.error, str)
    assert result.exit_reason == "success"


def test_extract_text_pi_live_filters_thinking_from_real_output(deepseek_events):
    thinking_events = [
        e for e in deepseek_events
        if e.get("assistantMessageEvent", {}).get("type") == "thinking_delta"
    ]
    text_events = [
        e for e in deepseek_events
        if e.get("assistantMessageEvent", {}).get("type") == "text_delta"
    ]
    assert thinking_events
    assert text_events

    for event in thinking_events:
        assert _extract_text_pi_live(event) == ""

    for event in text_events:
        delta = event["assistantMessageEvent"]["delta"]
        assert delta != ""
        assert _extract_text_pi_live(event) == delta


def test_extract_text_pi_final_excludes_thinking_from_real_output(deepseek_events):
    message_end_events = [
        e for e in deepseek_events
        if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
    ]
    assert message_end_events
    final_event = message_end_events[-1]

    text = _extract_text_pi_final(final_event)
    assert text != ""
    assert "thinkingSignature" not in text

    content = final_event["message"]["content"]
    assert any(block.get("type") == "thinking" for block in content)
    thinking_text = "".join(
        block.get("thinking", "") for block in content if block.get("type") == "thinking"
    )
    assert thinking_text not in text


def test_pi_harness_log_is_valid_jsonl(tmp_path):
    log_path = tmp_path / "pi.jsonl"
    result = PiHarness().run(
        "say hello",
        timeout=30,
        log_path=log_path,
        log_fields={"task_id": "t1", "job_id": "j1"},
    )
    assert result.success is True
    assert log_path.exists()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        rec = json.loads(line)  # raises if any line isn't valid JSON
        assert "timestamp" in rec
        assert "message" in rec
        assert "level" in rec
        assert rec["task_id"] == "t1"
        assert rec["job_id"] == "j1"


def test_pi_harness_output_has_no_thinking_artifacts(deepseek_events):
    result = PiHarness(model=DEEPSEEK_MODEL).run(
        "What is 2+2? Think briefly then answer.", timeout=40
    )
    assert result.success is True
    assert "thinkingSignature" not in result.output
    assert "thinking_delta" not in result.output
