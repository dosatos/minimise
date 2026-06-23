import pytest
from unittest.mock import Mock
import os
from minimise.plan_reviewer import PlanReviewer, ReviewFinding
from minimise.harness import AgentHarness, ClaudeCodeHarness, HarnessResult
from minimise.models import Plan


@pytest.fixture
def valid_plan():
    """A valid plan that might still have quality issues."""
    return Plan.model_validate({
        "name": "User Auth Implementation",
        "tasks": [
            {
                "id": "task-1",
                "name": "Database Schema",
                "description": "Add user table to database",
                "goal": "Store user data",
                "estimated_duration_min": 30
            },
            {
                "id": "task-2",
                "name": "Auth API",
                "description": "Create authentication endpoint",
                "goal": "Allow user login",
                "estimated_duration_min": 45
            }
        ]
    })


def make_harness(*outputs, success=True):
    """Build a Mock AgentHarness whose run() returns the given outputs in order.

    Each positional output yields a HarnessResult(success=success, output=...).
    """
    fake = Mock(spec=AgentHarness)
    fake.run.side_effect = [
        HarnessResult(success=success, output=out) for out in outputs
    ]
    return fake


class TestReviewFinding:
    """Test ReviewFinding data structure."""

    def test_finding_has_required_fields(self):
        """ReviewFinding has title, description, and suggestion."""
        finding = ReviewFinding(
            task_id="task-1",
            title="Missing auth context",
            description="This task involves API changes but doesn't clarify auth method",
            suggestion="Specify: JWT? API key? Existing middleware?"
        )
        assert finding.task_id == "task-1"
        assert finding.title == "Missing auth context"
        assert finding.description
        assert finding.suggestion


class TestPlanReviewerStructure:
    """Test PlanReviewer basic structure."""

    def test_reviewer_defaults_to_claude_code_harness(self):
        """Reviewer without an injected harness uses ClaudeCodeHarness."""
        reviewer = PlanReviewer()
        assert isinstance(reviewer.harness, ClaudeCodeHarness)

    def test_reviewer_stores_injected_harness(self):
        """Reviewer stores the harness it was constructed with."""
        fake = Mock(spec=AgentHarness)
        reviewer = PlanReviewer(harness=fake)
        assert reviewer.harness is fake

    def test_reviewer_has_review_method(self):
        """Reviewer has a review method that takes a plan."""
        reviewer = PlanReviewer(harness=Mock(spec=AgentHarness))
        assert hasattr(reviewer, 'review')
        assert callable(reviewer.review)


class TestPlanReviewerIntegration:
    """Test plan review with a mocked harness."""

    def test_review_returns_findings(self, valid_plan):
        """review() calls the harness and returns review findings."""
        fake = make_harness("""{
            "findings": [
                {
                    "task_id": "task-1",
                    "title": "Insufficient database schema details",
                    "description": "Task says 'Add user table' but doesn't specify columns",
                    "severity": "high",
                    "suggestion": "Specify columns and indexes"
                }
            ],
            "overall_quality": "medium",
            "blocking_issues": 1
        }""")

        reviewer = PlanReviewer(harness=fake)
        findings = reviewer.review(valid_plan)

        assert isinstance(findings, list)
        assert len(findings) == 1
        assert isinstance(findings[0], ReviewFinding)
        assert findings[0].task_id == "task-1"

    def test_review_handles_fenced_json(self, valid_plan):
        """review() parses JSON wrapped in ```json``` markdown fences."""
        fake = make_harness("""```json
{
    "findings": [
        {
            "task_id": "task-2",
            "title": "Auth method unspecified",
            "description": "Endpoint described without auth scheme",
            "severity": "medium",
            "suggestion": "Specify JWT vs session"
        }
    ],
    "overall_quality": "medium",
    "blocking_issues": 0
}
```""")

        reviewer = PlanReviewer(harness=fake)
        findings = reviewer.review(valid_plan)

        assert len(findings) == 1
        assert findings[0].task_id == "task-2"

    def test_review_handles_empty_findings(self, valid_plan):
        """review() returns empty list if plan has no issues."""
        fake = make_harness("""{
            "findings": [],
            "overall_quality": "high",
            "blocking_issues": 0
        }""")

        reviewer = PlanReviewer(harness=fake)
        findings = reviewer.review(valid_plan)

        assert findings == []

    def test_review_passes_plan_to_harness(self, valid_plan):
        """review() sends plan content to the harness prompt (first positional arg)."""
        fake = make_harness('{"findings": [], "overall_quality": "high", "blocking_issues": 0}')

        reviewer = PlanReviewer(harness=fake)
        reviewer.review(valid_plan)

        assert fake.run.called
        call_args = fake.run.call_args
        prompt = call_args.args[0]
        assert "User Auth Implementation" in prompt

    def test_review_retries_once_on_unparseable_then_succeeds(self, valid_plan):
        """Unparseable first response triggers exactly one retry, then parses."""
        fake = make_harness(
            "this is not json at all",
            '{"findings": [{"task_id": "task-1", "title": "X", "description": "Y", "severity": "low"}], "overall_quality": "high", "blocking_issues": 0}',
        )

        reviewer = PlanReviewer(harness=fake)
        findings = reviewer.review(valid_plan)

        assert fake.run.call_count == 2
        assert len(findings) == 1
        assert findings[0].task_id == "task-1"

    def test_review_raises_when_unparseable_twice(self, valid_plan):
        """Two unparseable responses propagate the ValueError (hard gate)."""
        fake = make_harness("not json", "still not json")

        reviewer = PlanReviewer(harness=fake)
        with pytest.raises(ValueError):
            reviewer.review(valid_plan)

        assert fake.run.call_count == 2

    def test_review_raises_when_harness_fails(self, valid_plan):
        """A failed harness invocation raises ValueError (review cannot pass silently)."""
        fake = make_harness("", success=False)

        reviewer = PlanReviewer(harness=fake)
        with pytest.raises(ValueError):
            reviewer.review(valid_plan)


class TestPlanReviewerModelSelection:
    """Test model selection logic in review()."""

    def test_default_model_is_opus(self, valid_plan, monkeypatch):
        """Without Bedrock, the opus model is requested."""
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
        fake = make_harness('{"findings": [], "overall_quality": "high", "blocking_issues": 0}')

        reviewer = PlanReviewer(harness=fake)
        reviewer.review(valid_plan)

        assert fake.run.call_args.kwargs["model"] == "claude-opus-4-8"
        assert fake.run.call_args.kwargs["timeout"] == 300

    def test_timeout_overridable_via_env(self, valid_plan, monkeypatch):
        """PLAN_REVIEW_TIMEOUT_SEC overrides the default review timeout."""
        monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
        monkeypatch.setenv("PLAN_REVIEW_TIMEOUT_SEC", "450")
        fake = make_harness('{"findings": [], "overall_quality": "high", "blocking_issues": 0}')

        reviewer = PlanReviewer(harness=fake)
        reviewer.review(valid_plan)

        assert fake.run.call_args.kwargs["timeout"] == 450

    def test_bedrock_model_default(self, valid_plan, monkeypatch):
        """With Bedrock and no override, the default sonnet Bedrock id is used."""
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        fake = make_harness('{"findings": [], "overall_quality": "high", "blocking_issues": 0}')

        reviewer = PlanReviewer(harness=fake)
        reviewer.review(valid_plan)

        assert fake.run.call_args.kwargs["model"] == "us.anthropic.claude-sonnet-4-6"

    def test_bedrock_model_override(self, valid_plan, monkeypatch):
        """BEDROCK_MODEL_ID overrides the default Bedrock model."""
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        monkeypatch.setenv("BEDROCK_MODEL_ID", "us.anthropic.custom-model")
        fake = make_harness('{"findings": [], "overall_quality": "high", "blocking_issues": 0}')

        reviewer = PlanReviewer(harness=fake)
        reviewer.review(valid_plan)

        assert fake.run.call_args.kwargs["model"] == "us.anthropic.custom-model"
