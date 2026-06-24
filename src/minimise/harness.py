import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class HarnessResult:
    """Result of a single harness invocation."""

    success: bool
    output: str
    error: Optional[str] = None


class AgentHarness(ABC):
    """Abstract interface for sending a prompt to an agent harness."""

    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        timeout: int = 900,
        model: Optional[str] = None,
        allow_edits: bool = False,
    ) -> HarnessResult:
        """Send a prompt to the harness and return its text output.

        allow_edits=True permits the agent to modify files (adds
        --dangerously-skip-permissions). When False, the call is a
        read-only completion.
        """
        raise NotImplementedError


class ClaudeCodeHarness(AgentHarness):
    """AgentHarness backed by the `claude -p` CLI subprocess."""

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
        timeout: int = 900,
        model: Optional[str] = None,
        allow_edits: bool = False,
    ) -> HarnessResult:
        cmd = ["claude", "-p", "--output-format", "text"]
        if allow_edits:
            cmd.append("--dangerously-skip-permissions")
        if model is not None:
            cmd += ["--model", model]

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=self._build_env(),
            )

            if result.returncode == 0:
                return HarnessResult(success=True, output=result.stdout or "")
            return HarnessResult(
                success=False,
                output=result.stdout or "",
                error=result.stderr or "",
            )

        except subprocess.TimeoutExpired:
            return HarnessResult(success=False, output="", error=f"timeout after {timeout}s")
        except Exception as e:
            return HarnessResult(success=False, output="", error=str(e))
