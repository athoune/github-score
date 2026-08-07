"""Tests for the LLM provider prompt and JSON parsing (no network)."""

from unittest.mock import AsyncMock, patch

import pytest

from gh_score.config import LLMConfig
from gh_score.core.models import (
    LLMRecommendation,
    MaintenanceState,
    QualitativeSignals,
    RepoUrl,
    Repository,
)
from gh_score.llm.provider import (
    LLMError,
    _build_prompt,
    _build_report_digest,
    _build_recommendation_prompt,
    _denies_fact,
    _extract_json_object,
    _parse_qualitative,
    _parse_recommendation,
    _TEXT_MAINTENANCE_STATES,
    analyze_qualitative_with_llm,
    analyze_recommendation_with_llm,
)


class TestWarningsPropagation:
    """LLM failures must be reported through the warnings list."""

    def _repo_with_readme(self) -> Repository:
        repo = Repository(url=RepoUrl("owner", "repo"))
        repo.readme_content = "# demo\nActive project with a roadmap.\n"
        return repo

    @pytest.mark.asyncio
    async def test_failure_appends_warning_and_returns_empty(self):
        warnings: list[str] = []

        with patch(
            "gh_score.llm.provider.LLMProvider.extract_signals",
            new=AsyncMock(side_effect=LLMError("boom")),
        ):
            signals = await analyze_qualitative_with_llm(
                self._repo_with_readme(), LLMConfig(enabled=True), warnings
            )

        assert signals == QualitativeSignals()
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_success_appends_no_warning(self):
        warnings: list[str] = []

        with patch(
            "gh_score.llm.provider.LLMProvider.extract_signals",
            new=AsyncMock(
                return_value={"roadmap": "v2", "text_maintenance_state": "active"}
            ),
        ):
            signals = await analyze_qualitative_with_llm(
                self._repo_with_readme(), LLMConfig(enabled=True), warnings
            )

        assert signals.roadmap == "v2"
        assert warnings == []

    @pytest.mark.asyncio
    async def test_disabled_appends_no_warning(self):
        warnings: list[str] = []

        signals = await analyze_qualitative_with_llm(
            self._repo_with_readme(), LLMConfig(enabled=False), warnings
        )

        assert signals == QualitativeSignals()
        assert warnings == []


class TestExtractJsonObject:
    def test_pure_json(self):
        assert _extract_json_object('{"a": 1}') == {"a": 1}

    def test_json_fence(self):
        content = 'Here you go:\n```json\n{"a": 1}\n```\nDone.'
        assert _extract_json_object(content) == {"a": 1}

    def test_json_embedded_in_prose(self):
        content = "Sure! The answer is {\"level\": \"green\", \"message\": \"OK\"} hope that helps."
        assert _extract_json_object(content) == {"level": "green", "message": "OK"}

    def test_empty_and_invalid(self):
        assert _extract_json_object("") == {}
        assert _extract_json_object("no json here") == {}

    def test_non_dict_json_returns_empty(self):
        assert _extract_json_object("[1, 2, 3]") == {}



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


class TestRecommendationPromptHardening:
    """The recommendation prompt must forbid denying provided signals."""

    def test_prompt_forbids_denying_signals(self):
        prompt = _build_recommendation_prompt()
        assert "never claim that a provided signal is absent" in prompt
        assert "do not deny it" in prompt


class TestDeniesFact:
    """Windowed negation heuristic."""

    _COMMERCIAL = ("commercial", "paid")
    _NEGATIONS = ("no", "without", "absence", "absent", "lack", "lacks", "none")

    def test_negation_before_keyword(self):
        assert _denies_fact(
            "there is an absence of explicit commercial support",
            self._COMMERCIAL,
            self._NEGATIONS,
        )
        assert _denies_fact("the project has no roadmap", ("roadmap",), self._NEGATIONS)

    def test_keyword_followed_by_missing(self):
        assert _denies_fact(
            "commercial support is missing entirely", self._COMMERCIAL, self._NEGATIONS
        )

    def test_no_negation(self):
        assert not _denies_fact(
            "the project offers commercial support", self._COMMERCIAL, self._NEGATIONS
        )

    def test_negation_without_keyword(self):
        assert not _denies_fact("there is no doubt about it", self._COMMERCIAL, self._NEGATIONS)


class TestContradictionGuard:
    """The refined recommendation must not deny facts the analysis found."""

    def _result(self):
        from gh_score.core.models import (
            AnalysisResult,
            ContributorsIndicator,
            LanguagesIndicator,
            LicenseIndicator,
            MaintenanceIndicator,
            QualitativeIndicator,
            ReleaseHealthIndicator,
            RepositoryMeta,
            SustainabilityIndicator,
        )

        return AnalysisResult(
            url=RepoUrl("owner", "repo"),
            meta=RepositoryMeta(),
            release_health=ReleaseHealthIndicator(),
            license=LicenseIndicator(),
            contributors=ContributorsIndicator(),
            maintenance=MaintenanceIndicator(),
            languages=LanguagesIndicator(primary="Python"),
            sustainability=SustainabilityIndicator(has_funding=True),
            qualitative=QualitativeIndicator(
                commercial_support="managed cloud", available=True
            ),
        )

    @pytest.mark.asyncio
    async def test_denied_present_fact_appends_warning(self):
        warnings: list[str] = []
        result = self._result()

        with patch(
            "gh_score.llm.provider.LLMProvider.extract_signals",
            new=AsyncMock(
                return_value={
                    "level": "green",
                    "message": "Active project",
                    "explanation": (
                        "There is an absence of explicit commercial support, "
                        "which limits growth."
                    ),
                    "confidence": 0.7,
                }
            ),
        ):
            rec = await analyze_recommendation_with_llm(
                result, LLMConfig(enabled=True), warnings
            )

        assert rec is not None
        assert len(warnings) == 1
        assert "commercial" in warnings[0].lower()

    @pytest.mark.asyncio
    async def test_consistent_explanation_no_warning(self):
        warnings: list[str] = []
        result = self._result()

        with patch(
            "gh_score.llm.provider.LLMProvider.extract_signals",
            new=AsyncMock(
                return_value={
                    "level": "green",
                    "message": "Active project",
                    "explanation": "The project offers commercial support.",
                    "confidence": 0.7,
                }
            ),
        ):
            rec = await analyze_recommendation_with_llm(
                result, LLMConfig(enabled=True), warnings
            )

        assert rec is not None
        assert warnings == []

    @pytest.mark.asyncio
    async def test_absent_fact_not_checked(self):
        # No commercial support was extracted, so denying it is not a
        # contradiction — the guard must stay silent.
        warnings: list[str] = []
        result = self._result()
        from gh_score.core.models import QualitativeIndicator

        result.qualitative = QualitativeIndicator(available=False)

        with patch(
            "gh_score.llm.provider.LLMProvider.extract_signals",
            new=AsyncMock(
                return_value={
                    "level": "orange",
                    "message": "No commercial support",
                    "explanation": "The project has no commercial support.",
                    "confidence": 0.5,
                }
            ),
        ):
            rec = await analyze_recommendation_with_llm(
                result, LLMConfig(enabled=True), warnings
            )

        assert rec is not None
        assert warnings == []
