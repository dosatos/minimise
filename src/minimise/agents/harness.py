import json
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from minimise.logging.backend import JobLogBackend, JsonlLogBackend

# Canonical harness identifiers — use these everywhere instead of bare strings
# so grep/reference-finding stays precise as the registry grows.
HARNESS_CLAUDE = "claude"
HARNESS_PI = "pi"


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

    def wrap_prompt(self, prompt: str) -> str:
        """Optional: inject harness-specific instructions into the prompt.

        The default is a no-op. ClaudeCodeHarness overrides this to prepend
        the Claude-specific warnings; other harnesses inherit the no-op.
        """
        return prompt


class ClaudeCodeHarness(AgentHarness):
    """AgentHarness backed by the `claude -p` CLI subprocess."""

    def __init__(self, backend: Optional[JobLogBackend] = None) -> None:
        self._backend = backend or JsonlLogBackend()

    def wrap_prompt(self, prompt: str) -> str:
        return (
            "⚠️  CRITICAL: Do not create exploratory jobs with 'mini job new'. "
            "If you accidentally create any jobs (test plans, temporary "
            "explorations, etc.), delete them before finishing:\n"
            "   mini job delete <job_id>\n\n"
            "⚠️  COMMITS: If you create any git commits, do NOT add co-author "
            "trailers (no \"Co-Authored-By:\" lines, no \"Generated with Claude "
            "Code\" lines). Use a plain commit message only. Prefer to leave "
            "changes uncommitted — the orchestrator commits your work for you.\n\n"
            + prompt
        )

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
        # Translate canonical model string: strip provider/ prefix
        if model and "/" in model:
            model = model.split("/", 1)[1]

        # stream-json lets the orchestrator read assistant output live;
        # the CLI requires --verbose alongside it.
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose"]  # HARNESS_CLAUDE
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
            threading.Thread(target=_feed_subprocess_stdin, args=(proc, prompt), daemon=True).start()
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


class PiHarness(AgentHarness):
    """AgentHarness backed by the `pi --mode json` CLI subprocess."""

    def __init__(self, backend: Optional[JobLogBackend] = None) -> None:
        self._backend = backend or JsonlLogBackend()

    def wrap_prompt(self, prompt: str) -> str:
        # Pi has no harness-specific guard rails yet. ClaudeCodeHarness
        # prepends warnings against exploratory `mini job new` calls and
        # co-author commit trailers — those are Claude-specific behaviors
        # that pi does not exhibit. If pi-specific bad behaviors emerge
        # (e.g. writing session files into the repo), add warnings here.
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
        # Pi accepts provider/id format natively via --model; no translation needed.
        # Pass canonical model string through unchanged.

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

            threading.Thread(
                target=_feed_subprocess_stdin, args=(proc, prompt), daemon=True
            ).start()
            stderr_capture: list[str] = []
            stderr_thread = threading.Thread(
                target=lambda: stderr_capture.append(proc.stderr.read() if proc.stderr else ""),
                daemon=True,
            )
            stderr_thread.start()

            chunks: list[str] = []
            final_output: list[str] = [""]
            reader = threading.Thread(
                target=self._read_stdout_pi,
                args=(proc, chunks, final_output, log_path, log_fields, self._backend, log_filter),
            )
            reader.start()
            reader.join(timeout=timeout)
            if reader.is_alive():
                proc.kill()
                proc.wait()
                reader.join()
                return HarnessResult(
                    success=False,
                    output=final_output[0] or "".join(chunks),
                    error=f"timeout after {timeout}s",
                    exit_reason="timeout",
                )

            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            stderr_thread.join(timeout=10)
            output = final_output[0] or "".join(chunks)

            if proc.returncode == 0:
                return HarnessResult(success=True, output=output, exit_reason="success")
            stderr = stderr_capture[0] if stderr_capture else ""
            return HarnessResult(success=False, output=output, error=stderr or "", exit_reason="agent_error")

        except Exception as e:
            if proc is not None:
                proc.kill()
                proc.wait()
            return HarnessResult(success=False, output="", error=str(e), exit_reason="agent_error")

    @staticmethod
    def _read_stdout_pi(proc: "subprocess.Popen", chunks: list, final_output: list, log_path, log_fields, backend, log_filter=None) -> None:
        """Read pi's `--mode json` stdout line-by-line.

        Pi emits per-token text_delta events, so we buffer live chunks and
        flush to the log on newline boundaries (plus a length safety valve).
        The last message_end's full text becomes final_output[0], which wins
        over the joined live chunks so LoopEngine control-line extraction sees
        the complete, unfiltered message.
        """
        record = log_path is not None and log_fields is not None
        buf = ""
        for line in proc.stdout or []:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            live_text = _extract_text_pi_live(event)
            if live_text:
                chunks.append(live_text)
                if record:
                    buf += live_text
                    # Flush on newline boundaries (normal sentence/paragraph
                    # output) or when the buffer grows unreasonably large
                    # (e.g. a code block with no newlines).
                    while "\n" in buf:
                        line_text, buf = buf.split("\n", 1)
                        logged = log_filter(line_text) if log_filter else line_text
                        if logged:
                            backend.record(log_path, log_fields, logged)
                    if len(buf) > 4096:
                        logged = log_filter(buf) if log_filter else buf
                        if logged:
                            backend.record(log_path, log_fields, logged)
                        buf = ""

            final_text = _extract_text_pi_final(event)
            if final_text:
                final_output[0] = final_text

        # Flush anything left in the buffer after stdout closes.
        if record and buf:
            logged = log_filter(buf) if log_filter else buf
            if logged:
                backend.record(log_path, log_fields, logged)


_BUILDERS: dict[str, type[AgentHarness]] = {
    HARNESS_CLAUDE: ClaudeCodeHarness,
    HARNESS_PI: PiHarness,
}


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
            persona = self._personas.get(persona_name)
            if persona is not None and getattr(persona, "harness", None):
                return persona.harness
        return self._default_harness

    def resolve_for_task(self, task) -> str:
        """Extract harness and assignee from a Task dataclass."""
        return self.resolve(
            task_harness=getattr(task, "harness", None),
            persona_name=getattr(task, "assignee", None),
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
            persona = self._personas.get(persona_name)
            if persona is not None and persona.model:
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
        self._resolver = _NameResolver(
            personas or {},
            default_harness=default_harness,
            default_model=default_model,
        )
        self._backend = backend or JsonlLogBackend()

    def from_task(self, task) -> AgentHarness:
        """Resolve + instantiate the harness for a Task."""
        name = self._resolver.resolve_for_task(task)
        cls = _BUILDERS[name]
        return cls(backend=self._backend)

    def resolve_model(self, task) -> Optional[str]:
        """Resolve the model string for *task* using the resolution chain.

        Order: task.model → persona.model → factory default_model → None.
        """
        return self._resolver.resolve_model_for_task(task)
