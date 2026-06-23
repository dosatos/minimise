import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from minimise.harness import HarnessResult, AgentHarness, ClaudeCodeHarness


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

@patch("minimise.harness.subprocess.run")
def test_command_base_no_edits_no_model(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ClaudeCodeHarness().run("hi")
    cmd = mock_run.call_args.args[0]
    assert cmd == ["claude", "-p", "--output-format", "text"]


@patch("minimise.harness.subprocess.run")
def test_command_includes_skip_permissions_only_when_allow_edits(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    ClaudeCodeHarness().run("hi", allow_edits=False)
    assert "--dangerously-skip-permissions" not in mock_run.call_args.args[0]

    ClaudeCodeHarness().run("hi", allow_edits=True)
    assert "--dangerously-skip-permissions" in mock_run.call_args.args[0]


@patch("minimise.harness.subprocess.run")
def test_command_includes_model_only_when_given(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    ClaudeCodeHarness().run("hi")
    assert "--model" not in mock_run.call_args.args[0]

    ClaudeCodeHarness().run("hi", model="claude-opus-4-8")
    cmd = mock_run.call_args.args[0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"


@patch("minimise.harness.subprocess.run")
def test_run_passes_prompt_cwd_timeout(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ClaudeCodeHarness().run("the-prompt", cwd="/repo", timeout=120)
    kwargs = mock_run.call_args.kwargs
    assert kwargs["input"] == "the-prompt"
    assert kwargs["cwd"] == "/repo"
    assert kwargs["timeout"] == 120
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


# --- Result mapping ---

@patch("minimise.harness.subprocess.run")
def test_run_returncode_zero_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="all good", stderr="")
    result = ClaudeCodeHarness().run("hi")
    assert result == HarnessResult(success=True, output="all good")


@patch("minimise.harness.subprocess.run")
def test_run_returncode_zero_none_stdout_coalesced(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=None, stderr=None)
    result = ClaudeCodeHarness().run("hi")
    assert result.success is True
    assert result.output == ""


@patch("minimise.harness.subprocess.run")
def test_run_returncode_nonzero_failure(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="partial", stderr="boom")
    result = ClaudeCodeHarness().run("hi")
    assert result.success is False
    assert result.output == "partial"
    assert result.error == "boom"


@patch("minimise.harness.subprocess.run")
def test_run_returncode_nonzero_none_streams_coalesced(mock_run):
    mock_run.return_value = MagicMock(returncode=2, stdout=None, stderr=None)
    result = ClaudeCodeHarness().run("hi")
    assert result.success is False
    assert result.output == ""
    assert result.error == ""


@patch("minimise.harness.subprocess.run")
def test_run_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
    result = ClaudeCodeHarness().run("hi", timeout=300)
    assert result.success is False
    assert result.output == ""
    assert result.error == "timeout after 300s"


@patch("minimise.harness.subprocess.run")
def test_run_generic_exception(mock_run):
    mock_run.side_effect = FileNotFoundError("claude not found")
    result = ClaudeCodeHarness().run("hi")
    assert result.success is False
    assert result.output == ""
    assert result.error == "claude not found"
