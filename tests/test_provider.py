"""Tests for the LLM provider prompt and JSON parsing (no network)."""

from gh_score.core.models import (
    LLMRecommendation,
    MaintenanceState,
    QualitativeSignals,
)
from gh_score.llm.provider import (
    _build_prompt,
    _build_report_digest,
    _build_recommendation_prompt,
    _parse_qualitative,
    _parse_recommendation,
    _TEXT_MAINTENANCE_STATES,
)


class TestParseQualitative:
    def test_full_signals(self):
        raw = {
            "roadmap": "v2 with async support",
            "security_policy": "report via GitHub private advisory",
            "commercial_support": "paid support available",
            "text_maintenance_state": "active",
        }
        s = _parse_qualitative(raw)
        assert s == QualitativeSignals(
            roadmap="v2 with async support",
            security_policy="report via GitHub private advisory",
            commercial_support="paid support available",
            text_maintenance_state="active",
        )

    def test_null_and_missing_fields(self):
        s = _parse_qualitative({"roadmap": None})
        assert s.any is False

    def test_invalid_maintenance_state_rejected(self):
        s = _parse_qualitative({"text_maintenance_state": "kinda alive"})
        assert s.text_maintenance_state is None

    def test_state_values(self):
        assert _TEXT_MAINTENANCE_STATES == {"active", "maintenance", "abandoned", "unknown"}

    def test_blank_strings_become_none(self):
        s = _parse_qualitative({"roadmap": "  ", "commercial_support": ""})
        assert s.roadmap is None
        assert s.commercial_support is None


class TestPromptScope:
    """Regression: the LLM must not duplicate deterministic functions."""

    def test_prompt_excludes_sponsors_and_governance(self):
        prompt = _build_prompt("sustainability and governance")
        assert "sponsors" not in prompt
        assert "governance_model" not in prompt
        assert "roadmap" in prompt
        assert "text_maintenance_state" in prompt

    def test_prompt_contains_text_placeholder(self):
        prompt = _build_prompt("sustainability and governance")
        assert "{text}" in prompt


class TestParseRecommendation:
    def test_full(self):
        rec = _parse_recommendation({
            "level": "orange",
            "message": "Promising but young",
            "explanation": "Active but small community.",
            "confidence": "0.7",
        })
        assert rec == LLMRecommendation(
            level="orange",
            message="Promising but young",
            explanation="Active but small community.",
            confidence=0.7,
        )

    def test_invalid_level_becomes_empty(self):
        rec = _parse_recommendation({"level": "purple"})
        assert rec.level == ""

    def test_confidence_clamped_and_fallback(self):
        assert _parse_recommendation({"confidence": "1.7"}).confidence == 1.0
        assert _parse_recommendation({"confidence": "-0.2"}).confidence == 0.0
        assert _parse_recommendation({"confidence": "nope"}).confidence == 0.0
        assert _parse_recommendation({}).confidence == 0.0

    def test_blank_message_and_explanation(self):
        rec = _parse_recommendation({"message": "  ", "explanation": ""})
        assert rec.message == ""
        assert rec.explanation == ""


class TestReportDigest:
    def test_digest_contains_key_families(self):
        from gh_score.core.models import (
            AnalysisResult,
            ContributorsIndicator,
            LicenseIndicator,
            MaintenanceIndicator,
            ReleaseHealthIndicator,
            RepoUrl,
            RepositoryMeta,
            SustainabilityIndicator,
        )

        result = AnalysisResult(
            url=RepoUrl("owner", "repo"),
            meta=RepositoryMeta(owner="owner", owner_type="organization", stars=42),
            release_health=ReleaseHealthIndicator(),
            license=LicenseIndicator(),
            contributors=ContributorsIndicator(),
            maintenance=MaintenanceIndicator(),
            languages=None,  # type: ignore[arg-type]  # handled in digest
            sustainability=SustainabilityIndicator(),
        )
        digest = _build_report_digest(result)
        assert digest["stars"] == 42
        assert digest["owner_type"] == "organization"
        # maintenance state of the (empty) default indicator is UNKNOWN
        assert digest["maintenance"]["state"] == MaintenanceState.UNKNOWN.value
        assert digest["primary_language"] is None

    def test_recommendation_prompt_has_digest_placeholder(self):
        prompt = _build_recommendation_prompt()
        assert "{digest}" in prompt
        assert "green" in prompt
