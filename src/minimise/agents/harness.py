import json
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from minimise.logging.backend import JobLogBackend, JsonlLogBackend


@dataclass
class HarnessResult:
    """Result of a single harness invocation."""

    success: bool
    output: str
    error: Optional[str] = None
    exit_reason: str = ""


def _extract_text(event: dict) -> str:
    """Extract assistant text from a single stream-json event.

    Keep only ``assistant`` events and, from those, concatenate the ``text``
    fields of ``message.content`` blocks whose type is ``text``. Everything
    else (tool_use, system, result) yields "".
    """
    if event.get("type") != "assistant":
        return ""
    content = event.get("message", {}).get("content") or []
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


class AgentHarness(ABC):
    """Abstract interface for sending a prompt to an agent harness."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        allow_edits: bool = False,
        log_path: Optional[Union[str, Path]] = None,
        log_fields: Optional[dict] = None,
        log_filter: Optional[Callable[[str], str]] = None,
    ) -> HarnessResult:
        """Send a prompt to the harness and return its text output.

        allow_edits=True permits the agent to modify files (adds
        --dangerously-skip-permissions). When False, the call is a
        read-only completion.

        log_path + log_fields, when both given, write each extracted assistant
        chunk as a JSON line (log_fields merged with timestamp/level/message)
        via the injected backend so the run can be tailed/queried. If either is
        None, nothing is written and behavior is unchanged.

        log_filter, when given, transforms each chunk's text before it is
        recorded (result.output is unaffected). A chunk that filters to empty
        is skipped.
        """
        raise NotImplementedError


class ClaudeCodeHarness(AgentHarness):
    """AgentHarness backed by the `claude -p` CLI subprocess."""

    def __init__(self, backend: Optional[JobLogBackend] = None) -> None:
        self._backend = backend or JsonlLogBackend()

    def _build_env(self) -> dict:
        """Build secure environment for Claude Code subprocess.

        Only includes necessary env vars for Claude auth and PATH resolution.
        Excludes sensitive credentials and unnecessary variables.
        Uses either Anthropic API OR AWS Bedrock, never both (to avoid conflicts).
        """
        use_bedrock = os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1"

        # Common vars for all backends
        common_keys = {
            "PATH",           # Required to find claude command
            "HOME",           # Required for ~/.claude auth cache
            "USER",           # Context info
            "SHELL",          # Shell preferences
            "LANG",           # Locale
        }

        if use_bedrock:
            # Use only Bedrock credentials
            safe_keys = common_keys | {
                "CLAUDE_CODE_USE_BEDROCK",
                "AWS_REGION",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_BEARER_TOKEN_BEDROCK",
            }
        else:
            # Use only Anthropic credentials
            safe_keys = common_keys | {"ANTHROPIC_API_KEY"}

        return {k: v for k, v in os.environ.items() if k in safe_keys}

    def run(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        allow_edits: bool = False,
        log_path: Optional[Union[str, Path]] = None,
        log_fields: Optional[dict] = None,
        log_filter: Optional[Callable[[str], str]] = None,
    ) -> HarnessResult:
        # stream-json lets the orchestrator read assistant output live;
        # the CLI requires --verbose alongside it.
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
        if allow_edits:
            cmd.append("--dangerously-skip-permissions")
        if model is not None:
            cmd += ["--model", model]
        if system_prompt is not None:
            cmd += ["--system-prompt", system_prompt]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=cwd,
                env=self._build_env(),
            )

            # Feed stdin and drain stderr on separate threads so a large prompt
            # or a chatty subprocess can't deadlock against the stdout we read.
            threading.Thread(target=self._feed_stdin, args=(proc, prompt), daemon=True).start()
            stderr_capture: list[str] = []
            stderr_thread = threading.Thread(
                target=lambda: stderr_capture.append(proc.stderr.read() if proc.stderr else ""),
                daemon=True,
            )
            stderr_thread.start()

            chunks: list[str] = []
            reader = threading.Thread(
                target=self._read_stdout,
                args=(proc, chunks, log_path, log_fields, self._backend, log_filter),
            )
            reader.start()
            # Bound the live read with a real wall-clock deadline; the old
            # subprocess.run(timeout=) guarantee is otherwise lost (a hung agent
            # that holds stdout open would block the read loop forever).
            reader.join(timeout=timeout)
            if reader.is_alive():
                proc.kill()
                proc.wait()  # reap the killed child so it doesn't linger as a zombie
                reader.join()
                return HarnessResult(success=False, output="".join(chunks), error=f"timeout after {timeout}s", exit_reason="timeout")

            # stdout hit EOF; reap the child and drain stderr, but stay bounded.
            # A surviving grandchild can keep these pipes open and otherwise hang
            # the worker forever, defeating the timeout the read loop preserves.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            stderr_thread.join(timeout=10)  # daemon; safe to abandon if still stuck
            output = "".join(chunks)

            if proc.returncode == 0:
                return HarnessResult(success=True, output=output, exit_reason="success")
            stderr = stderr_capture[0] if stderr_capture else ""
            return HarnessResult(success=False, output=output, error=stderr or "", exit_reason="agent_error")

        except Exception as e:
            if proc is not None:
                proc.kill()
                proc.wait()  # reap the child and let the reader thread close the log sink
            return HarnessResult(success=False, output="", error=str(e), exit_reason="agent_error")

    @staticmethod
    def _feed_stdin(proc: "subprocess.Popen", prompt: str) -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    @staticmethod
    def _read_stdout(proc: "subprocess.Popen", chunks: list, log_path, log_fields, backend, log_filter=None) -> None:
        """Read stdout line-by-line, accumulate assistant text, record each chunk.

        Each chunk is written as a structured JSON line via the backend when both
        log_path and log_fields are given; otherwise nothing is written.
        """
        record = log_path is not None and log_fields is not None
        for line in proc.stdout or []:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = _extract_text(event)
            if not text:
                continue
            chunks.append(text)
            if record:
                logged = log_filter(text) if log_filter else text
                if logged:
                    backend.record(log_path, log_fields, logged)
