import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from minimise.agents.harness import (
    HarnessResult,
    AgentHarness,
    ClaudeCodeHarness,
    PiHarness,
    _extract_text,
    _extract_text_pi_live,
    _extract_text_pi_final,
)


# --- Fake Popen helper for stream-json ---

def _assistant_event(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def make_fake_popen(stdout_lines, *, returncode=0, stderr=""):
    """Build a fake subprocess.Popen factory yielding canned stream-json lines."""
    def factory(*args, **kwargs):
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = iter(stdout_lines)
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = stderr
        proc.returncode = returncode
        proc.wait.return_value = returncode
        return proc
    return factory


# --- HarnessResult dataclass ---

def test_harness_result_defaults():
    result = HarnessResult(success=True, output="hello")
    assert result.success is True
    assert result.output == "hello"
    assert result.error is None


# --- AgentHarness abstract interface ---

def test_agent_harness_is_abstract():
    with pytest.raises(TypeError):
        AgentHarness()


def test_wrap_prompt_default_is_noop():
    class _StubHarness(AgentHarness):
        def run(self, prompt, **kwargs):
            return HarnessResult(success=True, output="")

    assert _StubHarness().wrap_prompt("x") == "x"


def test_claude_code_harness_wrap_prompt_prepends_warnings():
    wrapped = ClaudeCodeHarness().wrap_prompt("hello")
    assert "hello" in wrapped
    assert "Do not create exploratory jobs" in wrapped


def test_pi_harness_wrap_prompt_is_noop():
    """PiHarness.wrap_prompt is an explicit no-op — pi has no harness-specific
    guard rails yet (see comment in the source)."""
    from minimise.agents.harness import PiHarness
    assert PiHarness().wrap_prompt("x") == "x"


# --- _build_env: Bedrock path ---

@patch.dict(
    os.environ,
    {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "USER": "u",
        "SHELL": "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "akid",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_SESSION_TOKEN": "token",
        "AWS_BEARER_TOKEN_BEDROCK": "bearer",
        "ANTHROPIC_API_KEY": "should-not-appear",
        "SOME_OTHER_SECRET": "nope",
    },
    clear=True,
)
def test_build_env_bedrock_includes_aws_excludes_anthropic():
    env = ClaudeCodeHarness()._build_env()

    # common keys
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/u"
    assert env["USER"] == "u"
    assert env["SHELL"] == "/bin/zsh"
    assert env["LANG"] == "en_US.UTF-8"

    # bedrock credential set
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "us-east-1"
    assert env["AWS_ACCESS_KEY_ID"] == "akid"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert env["AWS_SESSION_TOKEN"] == "token"
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == "bearer"

    # anthropic key excluded on bedrock path
    assert "ANTHROPIC_API_KEY" not in env
    # unrelated env vars filtered out
    assert "SOME_OTHER_SECRET" not in env


# --- _build_env: Anthropic path ---

@patch.dict(
    os.environ,
    {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "USER": "u",
        "SHELL": "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "ANTHROPIC_API_KEY": "sk-ant-123",
        "AWS_ACCESS_KEY_ID": "should-not-appear",
        "AWS_SECRET_ACCESS_KEY": "should-not-appear",
        "AWS_REGION": "should-not-appear",
        "SOME_OTHER_SECRET": "nope",
    },
    clear=True,
)
def test_build_env_anthropic_includes_key_excludes_aws():
    env = ClaudeCodeHarness()._build_env()

    assert env["ANTHROPIC_API_KEY"] == "sk-ant-123"
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "AWS_REGION" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "SOME_OTHER_SECRET" not in env
    # common keys still present
    assert env["PATH"] == "/usr/bin"


@patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "0", "ANTHROPIC_API_KEY": "k"}, clear=True)
def test_build_env_bedrock_flag_not_one_uses_anthropic():
    env = ClaudeCodeHarness()._build_env()
    assert "ANTHROPIC_API_KEY" in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


# --- Command construction ---

@patch("minimise.agents.harness.subprocess.Popen")
def test_command_base_no_edits_no_model(mock_popen):
    mock_popen.side_effect = make_fake_popen([])
    ClaudeCodeHarness().run("hi")
    cmd = mock_popen.call_args.args[0]
    assert cmd == ["claude", "-p", "--output-format", "stream-json", "--verbose"]


@patch("minimise.agents.harness.subprocess.Popen")
def test_command_includes_skip_permissions_only_when_allow_edits(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    ClaudeCodeHarness().run("hi", allow_edits=False)
    assert "--dangerously-skip-permissions" not in mock_popen.call_args.args[0]

    ClaudeCodeHarness().run("hi", allow_edits=True)
    assert "--dangerously-skip-permissions" in mock_popen.call_args.args[0]


@patch("minimise.agents.harness.subprocess.Popen")
def test_command_includes_model_only_when_given(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    ClaudeCodeHarness().run("hi")
    assert "--model" not in mock_popen.call_args.args[0]

    ClaudeCodeHarness().run("hi", model="claude-opus-4-8")
    cmd = mock_popen.call_args.args[0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"


@patch("minimise.agents.harness.subprocess.Popen")
def test_command_strips_provider_prefix_from_model(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    ClaudeCodeHarness().run("hi", model="anthropic/claude-sonnet-4-8")
    cmd = mock_popen.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-8"

    ClaudeCodeHarness().run("hi", model="claude-sonnet-4-8")
    cmd = mock_popen.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-8"


@patch("minimise.agents.harness.subprocess.Popen")
def test_command_includes_system_prompt_only_when_given(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    ClaudeCodeHarness().run("hi")
    assert "--system-prompt" not in mock_popen.call_args.args[0]

    ClaudeCodeHarness().run("hi", system_prompt="PERSONA TEXT")
    cmd = mock_popen.call_args.args[0]
    assert "--system-prompt" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "PERSONA TEXT"


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_passes_prompt_cwd(mock_popen):
    mock_popen.side_effect = make_fake_popen([])
    ClaudeCodeHarness().run("the-prompt", cwd="/repo", timeout=120)
    kwargs = mock_popen.call_args.kwargs
    assert kwargs["cwd"] == "/repo"
    assert kwargs["text"] is True


# --- _extract_text robustness against malformed events ---

@pytest.mark.parametrize("event", [
    {"type": "assistant", "message": {"content": None}},
    {"type": "assistant", "message": {"content": "oops"}},
    {"type": "assistant", "message": {"content": ["not-a-dict", 42]}},
    {"type": "assistant", "message": {}},
    {"type": "assistant"},
])
def test_extract_text_tolerates_malformed_content(event):
    # Must not raise on unexpected content shapes (guards the live read loop,
    # which only catches JSONDecodeError).
    assert _extract_text(event) == ""


# --- stream-json parsing / result mapping ---

@patch("minimise.agents.harness.subprocess.Popen")
def test_run_extracts_only_assistant_text(mock_popen):
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(_assistant_event("Hello ")),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}),
        json.dumps(_assistant_event("world")),
        json.dumps({"type": "result", "result": "done"}),
    ]
    mock_popen.side_effect = make_fake_popen(lines, returncode=0)
    result = ClaudeCodeHarness().run("hi")
    assert result.success is True
    assert result.output == "Hello world"


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_writes_structured_jsonl_with_merged_fields(mock_popen, tmp_path):
    lines = [
        json.dumps(_assistant_event("first")),
        json.dumps(_assistant_event("second")),
    ]
    mock_popen.side_effect = make_fake_popen(lines, returncode=0)
    log = tmp_path / "job.log"
    fields = {"execution_id": "job#j1#task#task-9f#attempt#0", "type": "task"}
    result = ClaudeCodeHarness().run("hi", log_path=log, log_fields=fields)
    assert result.output == "firstsecond"

    records = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    assert len(records) == 2
    first = records[0]
    # caller fields merged in, plus timestamp/level/message
    assert first["execution_id"] == "job#j1#task#task-9f#attempt#0"
    assert first["type"] == "task"
    assert first["level"] == "info"
    assert first["message"] == "first"
    assert "timestamp" in first
    assert records[1]["message"] == "second"


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_no_log_path_writes_nothing(mock_popen, tmp_path):
    mock_popen.side_effect = make_fake_popen([json.dumps(_assistant_event("x"))])
    ClaudeCodeHarness().run("hi", log_fields={"type": "task"})
    # nothing created in tmp_path
    assert list(tmp_path.iterdir()) == []


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_no_log_fields_writes_nothing(mock_popen, tmp_path):
    # log_path given but log_fields=None (e.g. a review hook) → nothing written.
    mock_popen.side_effect = make_fake_popen([json.dumps(_assistant_event("x"))])
    log = tmp_path / "job.log"
    ClaudeCodeHarness().run("hi", log_path=log)
    assert not log.exists()


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_uses_injected_backend(mock_popen):
    # The harness routes writes through the injected backend, not a hard-coded one.
    from minimise.logging.backend import JobLogBackend

    calls = []

    class _SpyBackend(JobLogBackend):
        def record(self, log_path, fields, text, level="info"):
            calls.append((str(log_path), dict(fields), text, level))

        def search(self, log_path, query):
            return iter([])

        def matches(self, query, rec):
            return True

    mock_popen.side_effect = make_fake_popen([json.dumps(_assistant_event("hi"))])
    ClaudeCodeHarness(backend=_SpyBackend()).run(
        "p", log_path="/tmp/j.log", log_fields={"type": "task"}
    )
    assert calls == [("/tmp/j.log", {"type": "task"}, "hi", "info")]


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_skips_malformed_lines(mock_popen):
    lines = ["not json", "", json.dumps(_assistant_event("ok"))]
    mock_popen.side_effect = make_fake_popen(lines)
    result = ClaudeCodeHarness().run("hi")
    assert result.output == "ok"


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_returncode_nonzero_failure(mock_popen):
    mock_popen.side_effect = make_fake_popen(
        [json.dumps(_assistant_event("partial"))], returncode=1, stderr="boom"
    )
    result = ClaudeCodeHarness().run("hi")
    assert result.success is False
    assert result.output == "partial"
    assert result.error == "boom"


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_timeout(mock_popen):
    # Simulate a hung agent: stdout blocks (never yields, never EOFs) so the
    # reader thread can't finish within the deadline and the process is killed.
    import threading

    released = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            return self
        def __next__(self):
            released.wait()  # block until proc.kill() releases us
            raise StopIteration

    def factory(*args, **kwargs):
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = _BlockingStdout()
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = ""
        proc.kill.side_effect = lambda: released.set()
        return proc
    mock_popen.side_effect = factory
    result = ClaudeCodeHarness().run("hi", timeout=0.2)
    assert result.success is False
    assert result.output == ""
    assert result.error == "timeout after 0.2s"


# --- exit_reason classification ---

@patch("minimise.agents.harness.subprocess.Popen")
def test_exit_reason_success(mock_popen):
    mock_popen.side_effect = make_fake_popen([json.dumps(_assistant_event("ok"))], returncode=0)
    res = ClaudeCodeHarness().run("hi")
    assert res.success
    assert res.exit_reason == "success"


@patch("minimise.agents.harness.subprocess.Popen")
def test_exit_reason_agent_error_on_nonzero(mock_popen):
    mock_popen.side_effect = make_fake_popen([], returncode=1, stderr="boom")
    res = ClaudeCodeHarness().run("hi")
    assert not res.success
    assert res.exit_reason == "agent_error"


@patch("minimise.agents.harness.subprocess.Popen")
def test_exit_reason_timeout(mock_popen):
    import threading

    released = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            return self
        def __next__(self):
            released.wait()
            raise StopIteration

    def factory(*args, **kwargs):
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = _BlockingStdout()
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = ""
        proc.kill.side_effect = lambda: released.set()
        return proc
    mock_popen.side_effect = factory
    res = ClaudeCodeHarness().run("hi", timeout=0.2)
    assert not res.success
    assert res.exit_reason == "timeout"


@patch("minimise.agents.harness.subprocess.Popen")
def test_run_generic_exception(mock_popen):
    mock_popen.side_effect = FileNotFoundError("claude not found")
    result = ClaudeCodeHarness().run("hi")
    assert result.success is False
    assert result.output == ""
    assert result.error == "claude not found"


# --- _extract_text_pi_live (pi --mode json live streaming) ---

def test_extract_text_pi_live_returns_delta_on_text_delta():
    event = {
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_delta", "delta": "Hello"},
    }
    assert _extract_text_pi_live(event) == "Hello"


def test_extract_text_pi_live_discards_thinking_delta():
    event = {
        "type": "message_update",
        "assistantMessageEvent": {"type": "thinking_delta", "delta": "pondering..."},
    }
    assert _extract_text_pi_live(event) == ""


def test_extract_text_pi_live_ignores_non_message_update_event():
    event = {"type": "message_start"}
    assert _extract_text_pi_live(event) == ""


def test_extract_text_pi_live_tolerates_missing_assistant_message_event():
    event = {"type": "message_update"}
    assert _extract_text_pi_live(event) == ""


# --- _extract_text_pi_final (pi --mode json message_end) ---

def test_extract_text_pi_final_returns_full_text_from_message_end():
    event = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello there"}],
        },
    }
    assert _extract_text_pi_final(event) == "Hello there"


def test_extract_text_pi_final_skips_thinking_blocks():
    event = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "text": "let me think..."},
                {"type": "text", "text": "the answer"},
            ],
        },
    }
    assert _extract_text_pi_final(event) == "the answer"


def test_extract_text_pi_final_ignores_user_message_end():
    event = {
        "type": "message_end",
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    }
    assert _extract_text_pi_final(event) == ""


def test_extract_text_pi_final_ignores_non_message_end_event():
    event = {"type": "message_update"}
    assert _extract_text_pi_final(event) == ""


# --- PiHarness ---

def _pi_delta_event(text):
    return {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text}}


def _pi_final_event(text):
    return {
        "type": "message_end",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


@patch.dict(
    os.environ,
    {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "ANTHROPIC_API_KEY": "sk-ant-123",
        "SOME_OTHER_SECRET": "nope",
    },
    clear=True,
)
def test_pi_build_env_includes_common_and_provider_keys():
    env = PiHarness()._build_env()
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/u"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-123"
    assert "SOME_OTHER_SECRET" not in env


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_run_extracts_text_via_final_event(mock_popen):
    lines = [
        json.dumps(_pi_delta_event("Hello ")),
        json.dumps(_pi_delta_event("world")),
        json.dumps(_pi_final_event("Hello world")),
    ]
    mock_popen.side_effect = make_fake_popen(lines, returncode=0)
    result = PiHarness().run("hi")
    assert result.success is True
    assert result.output == "Hello world"


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_run_falls_back_to_joined_chunks_without_final_event(mock_popen):
    lines = [json.dumps(_pi_delta_event("partial "))]
    mock_popen.side_effect = make_fake_popen(lines, returncode=0)
    result = PiHarness().run("hi")
    assert result.output == "partial "


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_run_timeout(mock_popen):
    import threading

    released = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            return self
        def __next__(self):
            released.wait()
            raise StopIteration

    def factory(*args, **kwargs):
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = _BlockingStdout()
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = ""
        proc.kill.side_effect = lambda: released.set()
        return proc
    mock_popen.side_effect = factory
    result = PiHarness().run("hi", timeout=0.2)
    assert result.success is False
    assert result.exit_reason == "timeout"


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_run_returncode_nonzero_failure(mock_popen):
    mock_popen.side_effect = make_fake_popen(
        [json.dumps(_pi_final_event("partial"))], returncode=1, stderr="boom"
    )
    result = PiHarness().run("hi")
    assert result.success is False
    assert result.exit_reason == "agent_error"
    assert result.error == "boom"


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_command_includes_write_tools_only_when_allow_edits(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    PiHarness().run("hi", allow_edits=False)
    cmd = mock_popen.call_args.args[0]
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == "read,grep,find,ls"

    PiHarness().run("hi", allow_edits=True)
    cmd = mock_popen.call_args.args[0]
    assert cmd[cmd.index("--tools") + 1] == "read,bash,edit,write,grep,find,ls"


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_command_includes_model_only_when_given(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    PiHarness().run("hi")
    assert "--model" not in mock_popen.call_args.args[0]

    PiHarness().run("hi", model="gpt-5")
    cmd = mock_popen.call_args.args[0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5"


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_command_passes_canonical_model_through_unchanged(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    PiHarness().run("hi", model="anthropic/claude-sonnet-4-8")
    cmd = mock_popen.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "anthropic/claude-sonnet-4-8"

    PiHarness().run("hi", model="openai/gpt-4o")
    cmd = mock_popen.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "openai/gpt-4o"


@patch("minimise.agents.harness.subprocess.Popen")
def test_pi_command_includes_system_prompt_only_when_given(mock_popen):
    mock_popen.side_effect = make_fake_popen([])

    PiHarness().run("hi")
    assert "--system-prompt" not in mock_popen.call_args.args[0]

    PiHarness().run("hi", system_prompt="PERSONA")
    cmd = mock_popen.call_args.args[0]
    assert "--system-prompt" in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "PERSONA"
