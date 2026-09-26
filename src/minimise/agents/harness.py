import json
import os
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from minimise.logging.backend import JobLogBackend, JsonlLogBackend

# Canonical harness identifiers — use these everywhere instead of bare strings
# so grep/reference-finding stays precise as the registry grows.
HARNESS_CLAUDE = "claude"
HARNESS_PI = "pi"
HARNESS_CODEX = "codex"

# Install hints shown by HarnessNotFoundError; keep in sync with each
# harness's actual CLI package.
_INSTALL_HINTS = {
    HARNESS_CLAUDE: "npm install -g @anthropic-ai/claude-code",
    HARNESS_PI: "npm install -g @mariozechner/pi-coding-agent",
    HARNESS_CODEX: "npm install -g @openai/codex",
}


class HarnessNotFoundError(RuntimeError):
    """Raised when a resolved harness's binary isn't found on PATH."""

    def __init__(self, harness_name: str) -> None:
        self.harness_name = harness_name
        install_cmd = _INSTALL_HINTS.get(harness_name, f"install the '{harness_name}' CLI")
        super().__init__(
            f"Harness '{harness_name}' is not installed (binary not found on PATH).\n"
            f"  Install it: {install_cmd}\n"
            f"  Diagnose further: mini doctor"
        )


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


def _extract_text_pi_live(event: dict) -> str:
    """Extract text_delta from a message_update event (live streaming).

    Pi emits one JSON line per event. message_update events carry a
    nested assistantMessageEvent whose type discriminates text_delta
    (accumulate) from thinking_delta (discard). This function returns
    the delta text for text_delta events, or "" for everything else.
    """
    if event.get("type") != "message_update":
        return ""
    ame = event.get("assistantMessageEvent")
    if not isinstance(ame, dict):
        return ""
    if ame.get("type") == "text_delta":
        return ame.get("delta", "")
    return ""


def _extract_text_pi_final(event: dict) -> str:
    """Extract the FULL assistant text from a message_end event.

    Called once at the end of a turn. Reads the complete accumulated
    message (not deltas). Filters to role=="assistant", extracts
    content[].text blocks where type=="text", skips type=="thinking"
    blocks. Returns the concatenated text.
    """
    if event.get("type") != "message_end":
        return ""
    msg = event.get("message") or {}
    if msg.get("role") != "assistant":
        return ""
    content = msg.get("content") or []
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _extract_text_codex(event: dict) -> str:
    """Extract the completed Codex agent message from a JSONL event."""
    if event.get("type") != "item.completed":
        return ""
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return ""
    text = item.get("text", "")
    return text if isinstance(text, str) else ""


def _extract_error_codex(event: dict) -> str:
    """Extract a diagnostic from Codex error events for failed runs."""
    message = None
    if event.get("type") == "error":
        message = event.get("message")
    elif event.get("type") == "item.completed":
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "error":
            message = item.get("message")
    elif event.get("type") == "turn.failed":
        error = event.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        else:
            message = error
    return message if isinstance(message, str) else ""


def _strip_provider_prefix(model: Optional[str]) -> Optional[str]:
    """Translate the shared provider/model form to CLIs that want a bare ID."""
    if model and "/" in model:
        return model.split("/", 1)[1]
    return model


def _wrap_with_orchestration_guardrails(prompt: str) -> str:
    return (
        "⚠️  CRITICAL: Do not create exploratory jobs with 'mini job new'. "
        "If you accidentally create any jobs (test plans, temporary "
        "explorations, etc.), delete them before finishing:\n"
        "   mini job delete <job_id>\n\n"
        "⚠️  COMMITS: Do not create git commits or add co-author/generated-by "
        "trailers. Leave changes uncommitted — the orchestrator commits your "
        "work after the task succeeds.\n\n"
        + prompt
    )


def _feed_subprocess_stdin(proc: "subprocess.Popen", prompt: str) -> None:
    """Feed *prompt* to *proc*'s stdin on a best-effort basis."""
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass


class AgentHarness(ABC):
    """Abstract interface for sending a prompt to an agent harness."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        system_prompt: Optional[str] = None,
        allow_edits: bool = False,
        log_path: Optional[Union[str, Path]] = None,
        log_fields: Optional[dict] = None,
        log_filter: Optional[Callable[[str], str]] = None,
    ) -> HarnessResult:
        """Send a prompt to the harness and return its text output.

        allow_edits=True permits the agent to modify files using the
        harness's unattended execution policy. When False, the call is a
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

    def wrap_prompt(self, prompt: str) -> str:
        """Optional: inject harness-specific instructions into the prompt.

        The default is a no-op. Harnesses can override this to prepend
        orchestration-specific guardrails.
        """
        return prompt


@dataclass
class _StreamState:
    chunks: list[str] = field(default_factory=list)
    final_output: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    fatal_error: Optional[str] = None


class _JsonlCapture(ABC):
    """Harness-specific event handling over a shared JSONL process runner."""

    def __init__(
        self,
        backend: JobLogBackend,
        log_path: Optional[Union[str, Path]],
        log_fields: Optional[dict],
        log_filter: Optional[Callable[[str], str]],
    ) -> None:
        self.state = _StreamState()
        self._backend = backend
        self._log_path = log_path
        self._log_fields = log_fields
        self._log_filter = log_filter

    @property
    def output(self) -> str:
        if self.state.final_output is not None:
            return self.state.final_output
        return "".join(self.state.chunks)

    @property
    def error(self) -> str:
        return "\n".join(self.state.errors)

    @property
    def fatal_error(self) -> Optional[str]:
        return self.state.fatal_error

    def _append(self, text: str, *, record: bool = True) -> None:
        self.state.chunks.append(text)
        if record:
            self._record(text)

    def _record(self, text: str) -> None:
        if self._log_path is None or self._log_fields is None:
            return
        logged = self._log_filter(text) if self._log_filter else text
        if logged:
            self._backend.record(self._log_path, self._log_fields, logged)

    def finish(self) -> None:
        """Flush any buffered log text after stdout closes."""

    @abstractmethod
    def consume(self, event: dict) -> None:
        """Consume one decoded JSONL event."""
        raise NotImplementedError


class _ClaudeCapture(_JsonlCapture):
    def consume(self, event: dict) -> None:
        text = _extract_text(event)
        if text:
            self._append(text)


class _PiCapture(_JsonlCapture):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._buffer = ""

    def consume(self, event: dict) -> None:
        live_text = _extract_text_pi_live(event)
        if live_text:
            self._append(live_text, record=False)
            self._buffer += live_text
            self._flush_complete_lines()
            if len(self._buffer) > 4096:
                self._record(self._buffer)
                self._buffer = ""

        final_text = _extract_text_pi_final(event)
        if final_text:
            self.state.final_output = final_text

    def _flush_complete_lines(self) -> None:
        while "\n" in self._buffer:
            line_text, self._buffer = self._buffer.split("\n", 1)
            self._record(line_text)

    def finish(self) -> None:
        if self._buffer:
            self._record(self._buffer)
            self._buffer = ""


class _CodexCapture(_JsonlCapture):
    def consume(self, event: dict) -> None:
        text = _extract_text_codex(event)
        if text:
            self._append(text)
            # Codex agent_message items are complete messages, not deltas. The
            # latest one is the turn result; earlier ones remain in the live log.
            self.state.final_output = text

        error = _extract_error_codex(event)
        if error:
            self.state.errors.append(error)
            if event.get("type") in {"error", "turn.failed"}:
                self.state.fatal_error = error


def _read_jsonl_stdout(
    proc: "subprocess.Popen",
    capture: _JsonlCapture,
    reader_errors: list[str],
) -> None:
    """Decode stdout line-by-line and delegate events to the harness capture."""
    try:
        for raw_line in proc.stdout or []:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            capture.consume(event)
    except Exception as e:
        reader_errors.append(str(e))
    finally:
        try:
            capture.finish()
        except Exception as e:
            reader_errors.append(str(e))


def _drain_stderr(proc: "subprocess.Popen", target: list[str]) -> None:
    target.append(proc.stderr.read() if proc.stderr else "")


def _kill_and_reap(proc: "subprocess.Popen") -> None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_jsonl_subprocess(
    cmd: list[str],
    prompt: str,
    *,
    cwd: Optional[str],
    env: dict,
    timeout: Optional[float],
    capture: _JsonlCapture,
) -> HarnessResult:
    """Run a JSONL-emitting CLI with bounded, deadlock-safe pipe handling."""
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
            env=env,
        )

        threading.Thread(
            target=_feed_subprocess_stdin, args=(proc, prompt), daemon=True
        ).start()
        stderr_capture: list[str] = []
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_capture), daemon=True
        )
        stderr_thread.start()

        reader_errors: list[str] = []
        reader = threading.Thread(
            target=_read_jsonl_stdout,
            args=(proc, capture, reader_errors),
            daemon=True,
        )
        reader.start()
        reader.join(timeout=timeout)
        if reader.is_alive():
            _kill_and_reap(proc)
            reader.join(timeout=10)
            return HarnessResult(
                success=False,
                output=capture.output,
                error=f"timeout after {timeout}s",
                exit_reason="timeout",
            )

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_and_reap(proc)
        stderr_thread.join(timeout=10)

        if reader_errors:
            return HarnessResult(
                success=False,
                output=capture.output,
                error=reader_errors[0],
                exit_reason="agent_error",
            )
        if capture.fatal_error is not None:
            return HarnessResult(
                success=False,
                output=capture.output,
                error=capture.fatal_error,
                exit_reason="agent_error",
            )
        if proc.returncode == 0:
            return HarnessResult(
                success=True, output=capture.output, exit_reason="success"
            )

        stderr = stderr_capture[0] if stderr_capture else ""
        return HarnessResult(
            success=False,
            output=capture.output,
            error=stderr or capture.error,
            exit_reason="agent_error",
        )
    except Exception as e:
        if proc is not None:
            _kill_and_reap(proc)
        return HarnessResult(
            success=False,
            output=capture.output,
            error=str(e),
            exit_reason="agent_error",
        )


class _JsonlSubprocessHarness(AgentHarness):
    """Shared process lifecycle for harness CLIs that emit JSONL events."""

    def __init__(
        self,
        backend: Optional[JobLogBackend] = None,
        model: Optional[str] = None,
    ) -> None:
        self._backend = backend or JsonlLogBackend()
        self._model = model

    @abstractmethod
    def _build_command(
        self, *, allow_edits: bool, system_prompt: Optional[str]
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def _build_env(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def _new_capture(
        self,
        log_path: Optional[Union[str, Path]],
        log_fields: Optional[dict],
        log_filter: Optional[Callable[[str], str]],
    ) -> _JsonlCapture:
        raise NotImplementedError

    def run(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        system_prompt: Optional[str] = None,
        allow_edits: bool = False,
        log_path: Optional[Union[str, Path]] = None,
        log_fields: Optional[dict] = None,
        log_filter: Optional[Callable[[str], str]] = None,
    ) -> HarnessResult:
        try:
            capture = self._new_capture(log_path, log_fields, log_filter)
            cmd = self._build_command(
                allow_edits=allow_edits, system_prompt=system_prompt
            )
            env = self._build_env()
        except Exception as e:
            return HarnessResult(
                success=False, output="", error=str(e), exit_reason="agent_error"
            )
        return _run_jsonl_subprocess(
            cmd,
            prompt,
            cwd=cwd,
            env=env,
            timeout=timeout,
            capture=capture,
        )


class ClaudeCodeHarness(_JsonlSubprocessHarness):
    """AgentHarness backed by the `claude -p` CLI subprocess."""

    def wrap_prompt(self, prompt: str) -> str:
        return _wrap_with_orchestration_guardrails(prompt)

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

    def _build_command(
        self,
        *,
        allow_edits: bool = False,
        system_prompt: Optional[str] = None,
    ) -> list[str]:
        # stream-json lets the orchestrator read assistant output live;
        # the CLI requires --verbose alongside it.
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]  # HARNESS_CLAUDE
        if allow_edits:
            cmd.append("--dangerously-skip-permissions")
        model = _strip_provider_prefix(self._model)
        if model is not None:
            cmd += ["--model", model]
        if system_prompt is not None:
            cmd += ["--system-prompt", system_prompt]
        return cmd

    def _new_capture(self, log_path, log_fields, log_filter) -> _JsonlCapture:
        return _ClaudeCapture(self._backend, log_path, log_fields, log_filter)


class PiHarness(_JsonlSubprocessHarness):
    """AgentHarness backed by the `pi --mode json` CLI subprocess."""

    def wrap_prompt(self, prompt: str) -> str:
        # Pi has no harness-specific guard rails yet. If pi-specific bad
        # behaviors emerge (e.g. writing session files into the repo), add
        # warnings here.
        return prompt

    def _build_env(self) -> dict:
        """Build secure environment for the pi subprocess.

        Whitelists PATH/HOME/etc plus provider credentials for every LLM
        backend pi recognises. pi also reads ~/.pi/agent/auth.json, so
        env vars are only needed when the user has not set up auth.json.
        """
        safe_keys = {"PATH", "HOME", "USER", "SHELL", "LANG", "PI_CODING_AGENT_DIR"}
        # Full set of provider env vars pi supports (see pi docs: providers.md).
        # Kept in sync with pi's env-api-keys.ts /auth.json key map.
        provider_keys = {
            # Anthropic
            "ANTHROPIC_API_KEY",
            # Azure
            "AZURE_OPENAI_API_KEY",
            # OpenAI
            "OPENAI_API_KEY",
            # DeepSeek
            "DEEPSEEK_API_KEY",
            # Google Gemini
            "GOOGLE_API_KEY",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_GENAI_USE_GENERATIVEAI",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            # Mistral
            "MISTRAL_API_KEY",
            # Groq
            "GROQ_API_KEY",
            # Cerebras
            "CEREBRAS_API_KEY",
            # Cloudflare
            "CLOUDFLARE_API_KEY",
            "CLOUDFLARE_ACCOUNT_ID",
            "CLOUDFLARE_GATEWAY_ID",
            # xAI
            "XAI_API_KEY",
            # OpenRouter
            "OPENROUTER_API_KEY",
            # Vercel AI Gateway
            "AI_GATEWAY_API_KEY",
            # ZAI
            "ZAI_API_KEY",
            # OpenCode
            "OPENCODE_API_KEY",
            # Hugging Face
            "HF_TOKEN",
            # Fireworks
            "FIREWORKS_API_KEY",
            # Kimi
            "KIMI_API_KEY",
            # MiniMax
            "MINIMAX_API_KEY",
            "MINIMAX_CN_API_KEY",
            # Xiaomi MiMo
            "XIAOMI_API_KEY",
            "XIAOMI_TOKEN_PLAN_CN_API_KEY",
            "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
            "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
            # AWS Bedrock
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_REGION",
        }
        return {k: v for k, v in os.environ.items() if k in (safe_keys | provider_keys)}

    def _build_command(
        self,
        *,
        allow_edits: bool = False,
        system_prompt: Optional[str] = None,
    ) -> list[str]:
        # Pi accepts provider/id format natively via --model; no translation needed.
        # Pass canonical model string through unchanged.
        model = self._model

        # Let pi use its default thinking level — the model/provider decides.
        # Our parsers already filter thinking blocks from logs (_extract_text_pi_live)
        # and from final output (_extract_text_pi_final), so thinking content is
        # handled correctly regardless. Forcing --thinking off would break
        # reasoning-native models (o1/o3) and degrade others unnecessarily.
        cmd = [
            "pi", "--mode", "json", "-p", "--no-session",  # HARNESS_PI
            "--no-extensions", "--no-skills", "--no-context-files",
        ]
        if allow_edits:
            cmd += ["--tools", "read,bash,edit,write,grep,find,ls"]
        else:
            cmd += ["--tools", "read,grep,find,ls"]
        if model is not None:
            cmd += ["--model", model]
        if system_prompt is not None:
            cmd += ["--system-prompt", system_prompt]
        return cmd

    def _new_capture(self, log_path, log_fields, log_filter) -> _JsonlCapture:
        return _PiCapture(self._backend, log_path, log_fields, log_filter)


class CodexHarness(_JsonlSubprocessHarness):
    """AgentHarness backed by the non-interactive `codex exec` CLI."""

    def wrap_prompt(self, prompt: str) -> str:
        return _wrap_with_orchestration_guardrails(prompt)

    def _build_env(self) -> dict:
        """Keep Codex auth/config and network plumbing without leaking the full env."""
        safe_keys = {
            "PATH",
            "HOME",
            "USER",
            "SHELL",
            "LANG",
            "TMPDIR",
            "TEMP",
            "TMP",
            "CODEX_HOME",
            "CODEX_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT_ID",
            "OPENAI_ORGANIZATION",
            "CODEX_CA_CERTIFICATE",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_ROLE_ARN",
        }
        return {k: v for k, v in os.environ.items() if k in safe_keys}

    def _build_command(
        self,
        *,
        allow_edits: bool = False,
        system_prompt: Optional[str] = None,
    ) -> list[str]:
        cmd = [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--color",
            "never",
        ]
        if allow_edits:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd += [
                "--sandbox",
                "read-only",
                "-c",
                'approval_policy="never"',
            ]

        model = _strip_provider_prefix(self._model)
        if model is not None:
            cmd += ["--model", model]
        if system_prompt is not None:
            cmd += [
                "-c",
                f"developer_instructions={json.dumps(system_prompt)}",
            ]

        # A lone "-" makes stdin the only prompt source and avoids shell
        # quoting limits for large task handovers.
        cmd.append("-")
        return cmd

    def _new_capture(self, log_path, log_fields, log_filter) -> _JsonlCapture:
        return _CodexCapture(self._backend, log_path, log_fields, log_filter)


_BUILDERS: dict[str, type[AgentHarness]] = {
    HARNESS_CLAUDE: ClaudeCodeHarness,
    HARNESS_PI: PiHarness,
    HARNESS_CODEX: CodexHarness,
}
SUPPORTED_HARNESSES = tuple(_BUILDERS)


class _NameResolver:
    """Resolve which harness name to use from context.

    Resolution order:
      1. Explicit per-task/step override  (PlanTask.harness / Worker.harness)
      2. Persona default                  (personas.yaml → persona.harness)
      3. Global default                   (constructor arg)
    """

    def __init__(
        self,
        personas: dict,
        *,
        default_harness: str = HARNESS_CLAUDE,
        default_model: Optional[str] = None,
    ) -> None:
        self._personas = personas
        self._default_harness = default_harness
        self._default_model = default_model

    def _get_persona(self, persona_name: str):
        """Look up persona_name, raising if it names a persona not in the registry."""
        persona = self._personas.get(persona_name)
        if persona is None:
            raise ValueError(f"unknown persona {persona_name!r}")
        return persona

    def resolve(
        self,
        *,
        task_harness: Optional[str] = None,
        persona_name: Optional[str] = None,
    ) -> str:
        # task.harness and task.assignee are validated as mutually exclusive
        # at plan parse time, so at most one of these branches fires.
        if task_harness:
            return task_harness
        if persona_name:
            persona = self._get_persona(persona_name)
            if getattr(persona, "harness", None):
                return persona.harness
        return self._default_harness

    def resolve_for_task(self, task) -> str:
        """Extract harness and assignee from a Task dataclass."""
        return self.resolve(
            task_harness=getattr(task, "harness", None),
            persona_name=getattr(task, "assignee", None),
        )

    def resolve_for_worker(self, worker) -> str:
        """Extract harness and persona from a LoopEngine Worker."""
        return self.resolve(
            task_harness=getattr(worker, "harness", None),
            persona_name=getattr(worker, "persona", None),
        )

    def resolve_model(
        self,
        *,
        task_model: Optional[str] = None,
        persona_name: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve which model to use from context.

        Resolution order:
          1. Explicit per-task model override  (Task.model)
          2. Persona default                   (personas.yaml → persona.model)
          3. Global default                    (constructor arg)
          4. None                              (no model configured anywhere)
        """
        if task_model:
            return task_model
        if persona_name:
            persona = self._get_persona(persona_name)
            if persona.model:
                return persona.model
        return self._default_model

    def resolve_model_for_task(self, task) -> Optional[str]:
        """Extract model and assignee from a Task dataclass."""
        return self.resolve_model(
            task_model=getattr(task, "model", None),
            persona_name=getattr(task, "assignee", None),
        )


class HarnessFactory:
    """Instantiate an AgentHarness for a Task."""

    def __init__(
        self,
        personas=None,
        *,
        default_harness: str = HARNESS_CLAUDE,
        default_model: Optional[str] = None,
        backend: Optional[JobLogBackend] = None,
    ) -> None:
        self._personas = personas or {}
        self._resolver = _NameResolver(
            self._personas,
            default_harness=default_harness,
            default_model=default_model,
        )
        self._backend = backend or JsonlLogBackend()

    def resolve_prompt_for_worker(self, worker) -> Optional[str]:
        """Resolve a Worker's system prompt: persona | prompt | prompt_file | None.

        Raises ValueError if worker.persona names a persona not in the registry.
        """
        if worker.persona:
            persona = self._personas.get(worker.persona)
            if persona is None:
                raise ValueError(f"unknown persona {worker.persona!r}")
            return persona.system_prompt
        if worker.prompt:
            return worker.prompt
        if worker.prompt_file:
            return Path(worker.prompt_file).read_text()
        return None

    def resolve_prompt_for_task(self, task) -> Optional[str]:
        """Resolve a Task's system prompt: persona.system_prompt (via assignee), else None.

        Raises ValueError if task.assignee names a persona not in the registry.
        """
        assignee = getattr(task, "assignee", None)
        if not assignee:
            return None
        persona = self._personas.get(assignee)
        if persona is None:
            raise ValueError(f"unknown persona {assignee!r}")
        return persona.system_prompt

    def _instantiate(self, name: str, model: Optional[str] = None) -> AgentHarness:
        """Look up, availability-check, and instantiate the harness registered under *name*."""
        if name not in _BUILDERS:
            raise ValueError(f"Unknown harness: {name!r}")
        if shutil.which(name) is None:
            raise HarnessNotFoundError(name)
        return _BUILDERS[name](backend=self._backend, model=model)

    def for_task(self, task) -> AgentHarness:
        """Resolve + instantiate the harness for a Task, with its model baked in.

        Order: task.harness/model → persona.harness/model → factory default → None.
        """
        name = self._resolver.resolve_for_task(task)
        model = self._resolver.resolve_model_for_task(task)
        return self._instantiate(name, model)

    def for_worker(self, worker) -> AgentHarness:
        """Resolve + instantiate the harness for a LoopEngine Worker, with its model baked in.

        Order: worker.harness/model → persona.harness/model → factory default → None.
        """
        name = self._resolver.resolve_for_worker(worker)
        model = self._resolver.resolve_model(
            task_model=worker.model, persona_name=worker.persona
        )
        return self._instantiate(name, model)
