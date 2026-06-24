"""Agent-based plan review via the AgentHarness for quality feedback."""

import json
import os
from typing import List, Optional
from dataclasses import dataclass

from minimise.agents.harness import AgentHarness, ClaudeCodeHarness, HarnessResult


@dataclass
class ReviewFinding:
    """A single finding from plan review."""
    task_id: str
    title: str
    description: str
    severity: str = "medium"  # low, medium, high
    suggestion: Optional[str] = None


class PlanReviewer:
    """Reviews plans via an AgentHarness for quality feedback."""

    def __init__(self, harness: Optional[AgentHarness] = None):
        """
        Initialize reviewer with an agent harness.

        Args:
            harness: AgentHarness used to run the review prompt. Defaults to
                ClaudeCodeHarness(). Authentication (Anthropic API vs AWS
                Bedrock) is handled by the harness via env passthrough.
        """
        self.harness = harness or ClaudeCodeHarness()

    def review(self, plan: "Plan") -> List[ReviewFinding]:
        """
        Review a plan via the agent harness.

        Args:
            plan: Plan object to review

        Returns:
            List of ReviewFinding objects

        Raises:
            ValueError: if the harness fails, or the response cannot be parsed
                after one retry. Never silently passes (hard quality gate).
        """
        # Format plan as readable text
        plan_text = self._format_plan(plan)

        # Determine model to use
        # For Bedrock: can be overridden via BEDROCK_MODEL_ID env var
        # Otherwise: uses claude-opus-4-8
        if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
            model = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
        else:
            model = "claude-opus-4-8"

        # First attempt
        result = self._run_review(plan_text, model)
        try:
            return self._parse_response(result.output)
        except ValueError:
            # Retry once on parse failure; a transient bad response shouldn't
            # block a valid plan, but two failures still fail loud (hard gate).
            result = self._run_review(plan_text, model)
            return self._parse_response(result.output)

    def _run_review(self, plan_text: str, model: str) -> HarnessResult:
        """Run the review prompt through the harness, failing loud on error.

        A harness failure means the review could not be verified, so we raise
        rather than let the plan pass silently.
        """
        # Wall-clock cap for the review call. Large plans need more than the
        # original 120s; overridable via PLAN_REVIEW_TIMEOUT_SEC.
        timeout = int(os.environ.get("PLAN_REVIEW_TIMEOUT_SEC", "300"))
        result = self.harness.run(
            self._build_review_prompt(plan_text),
            model=model,
            timeout=timeout,
        )
        if not result.success:
            raise ValueError(
                f"Plan review harness invocation failed; cannot verify plan quality. "
                f"Error: {result.error or result.output}"
            )
        return result

    def _format_plan(self, plan: "Plan") -> str:
        """Format a Plan as readable text."""
        lines = []
        lines.append(f"Plan: {plan.name}")
        description = getattr(plan, "description", None)
        if description:
            lines.append(f"Description: {description}")

        lines.append("\nTasks:")
        for task in plan.tasks:
            lines.append(f"\n  Task: {task.id} - {task.name}")
            lines.append(f"    Description: {task.description}")
            lines.append(f"    Goal: {task.goal}")
            lines.append(f"    Est. Duration: {task.estimated_duration_min} min")

        return "\n".join(lines)

    def _build_review_prompt(self, plan_text: str) -> str:
        """Build the prompt for Claude to review the plan.

        The reviewer is a PRAGMATIC blocking gate: it reports ONLY issues severe
        enough to make implementation fail or produce incorrect/destructive
        results. It must NOT report style, wording, or nice-to-have improvements,
        and it returns an empty findings list for a sound plan. This keeps the
        gate convergent (a good plan passes) rather than generating an endless
        stream of pedantic nits.
        """
        return f"""You are a pragmatic engineering reviewer acting as a BLOCKING quality gate
for an implementation plan. The plan only passes if you return zero findings, so
report ONLY issues that genuinely must be fixed before implementation.

Report a finding ONLY if it is one of these SEVERE / CRITICAL / IMPORTANT problems:
- A correctness bug: an instruction that, if followed, produces wrong behavior or
  fails at runtime (e.g. references a nonexistent function/variable, wrong API usage).
- A data-loss or destructive risk (e.g. a migration that can drop or corrupt data,
  a non-atomic schema rebuild).
- A missing step that makes a task unimplementable or leaves the build/tests broken.
- An internal contradiction (two instructions that cannot both be satisfied).
- A factually wrong claim about the codebase that would mislead the implementer.

Do NOT report (these are NOT findings — ignore them entirely):
- Style, wording, naming, formatting, or clarity preferences.
- "Could be more explicit", "consider adding", "for robustness", "best practice".
- Hard-coded counts/line numbers that the implementer can self-correct.
- Defensive/extra tests or checks that are nice-to-have but not required for correctness.
- Anything already adequately specified, even if terse.

If the plan has no such severe issues, return an empty findings list. That is the
expected outcome for a sound plan — do not invent problems to look thorough.

For each genuine finding, provide: the task ID, a short title, what is wrong and why
it breaks implementation, and a concrete fix. Use severity "high" for blocking
correctness/data-loss issues and "medium" only for important-but-narrower risks;
never emit "low".

Return your review as JSON in this exact format:
{{
  "findings": [
    {{
      "task_id": "task-1",
      "title": "Issue title",
      "description": "What is wrong and why it breaks implementation",
      "severity": "high|medium",
      "suggestion": "Concrete fix"
    }}
  ],
  "overall_quality": "low|medium|high",
  "blocking_issues": <number of high-severity issues>,
  "summary": "Brief overall assessment"
}}

Plan to review:
{plan_text}

Return ONLY valid JSON, no other text."""

    def _parse_response(self, response_text: str) -> List[ReviewFinding]:
        """Parse Claude's JSON response.

        Models frequently wrap JSON in markdown code fences (```json ... ```),
        so strip those before parsing. Raise on genuinely malformed responses
        rather than silently passing the review.
        """
        cleaned = self._extract_json(response_text)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Plan review returned an unparseable response; cannot verify plan quality. "
                f"Parse error: {e}"
            ) from e

        findings = []
        for item in data.get('findings', []):
            finding = ReviewFinding(
                task_id=item.get('task_id', 'plan'),
                title=item.get('title', ''),
                description=item.get('description', ''),
                severity=item.get('severity', 'medium'),
                suggestion=item.get('suggestion')
            )
            findings.append(finding)

        return findings

    def _extract_json(self, response_text: str) -> str:
        """Strip markdown code fences and surrounding prose from a JSON response."""
        text = response_text.strip()

        # Strip ```json ... ``` or ``` ... ``` fences
        if text.startswith("```"):
            # Drop the opening fence line (``` or ```json)
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            # Drop the closing fence
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
            return text.strip()

        # Fallback: extract the outermost JSON object if surrounded by prose
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]

        return text
