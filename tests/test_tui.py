"""Tests for the TUI dashboard renderers.

Renders panels for a fully-populated AnalysisResult and asserts on the
Rich objects (Panel.title, Text.plain) and on the captured console
output of the full dashboard. Locale is pinned so assertions match.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

from gh_score.cli.tui import (
    _render_contributors,
    _render_languages,
    _render_license,
    _render_llm_recommendation,
    _render_maintenance,
    _render_qualitative,
    _render_recommendation,
    _render_registries,
    _render_release_health,
    _render_sustainability,
    _render_warnings,
    _status_glyph,
    render_dashboard,
)
from gh_score.core.models import (
    AnalysisResult,
    ContributorDetail,
    ContributorsIndicator,
    ContributorArchetype,
    LanguagesIndicator,
    LicenseIndicator,
    LicenseFamily,
    LLMRecommendation,
    MaintenanceIndicator,
    MaintenanceState,
    QualitativeIndicator,
    Recommendation,
    RecommendationLevel,
    RegistryInfo,
    ReleaseHealthIndicator,
    RepoUrl,
    RepositoryMeta,
    Status,
    SustainabilityIndicator,
)
from gh_score.i18n import t


@pytest.fixture
def en_locale(monkeypatch):
    """Pin the locale to English so renderer labels are deterministic."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)


@pytest.fixture
def result() -> AnalysisResult:
    """A fully-populated AnalysisResult exercising every renderer branch."""
    now = datetime.now(timezone.utc)
    return AnalysisResult(
        url=RepoUrl("owner", "repo"),
        meta=RepositoryMeta(
            owner="owner",
            owner_type="organization",
            stars=1234,
            forks=56,
            description="A demo",
            created_at=now - timedelta(days=100),
        ),
        release_health=ReleaseHealthIndicator(
            latest_version="v1.0.0",
            latest_date=now - timedelta(days=10),
            age_days=10,
            cadence_days=14.0,
            semver_compliant=True,
            is_prerelease=True,
            status=Status.WARNING,
            interpretation="",
        ),
        license=LicenseIndicator(
            spdx_id="MIT",
            family=LicenseFamily.PERMISSIVE,
            osi_approved=True,
            status=Status.HEALTHY,
            interpretation="",
        ),
        contributors=ContributorsIndicator(
            total_authors=42,
            bus_factor=3,
            bot_ratio=0.2,
            lead=ContributorDetail(
                login="alice",
                commits=100,
                archetype=ContributorArchetype.LEAD,
            ),
            historical_lead=ContributorDetail(
                login="bob",
                commits=50,
                archetype=ContributorArchetype.HISTORICAL_LEAD,
            ),
            minor_count=5,
            activity_trend={"3m": 10, "12m": 100},
            status=Status.HEALTHY,
            interpretation="",
        ),
        maintenance=MaintenanceIndicator(
            state=MaintenanceState.ACTIVE,
            last_commit_days_ago=0,
            commits_per_month=2.5,
            issue_velocity_days=4,
            stale_issue_ratio=0.1,
            status=Status.HEALTHY,
            interpretation="",
        ),
        languages=LanguagesIndicator(
            primary="Python",
            breakdown={"Python": 80.0, "HTML": 20.0},
            ecosystem="python",
            interpretation="",
        ),
        sustainability=SustainabilityIndicator(
            has_funding=True,
            funding_platforms=["GitHub Sponsors"],
            corporate_backing="Acme Corp",
            governance_model="Core team",
            status=Status.HEALTHY,
            interpretation="",
        ),
        registries=[
            RegistryInfo(
                ecosystem="pypi",
                package_name="mypkg",
                exists=True,
                latest_version="1.0",
                downloads=2_500_000,
                registry_license="MIT",
                license_matches_github=True,
            ),
            RegistryInfo(ecosystem="npm", package_name="other", exists=False),
        ],
        recommendation=Recommendation(
            level=RecommendationLevel.GREEN,
            message="Active project",
            confidence=1.0,
            reasoning=["active state, regular development", "42 authors"],
        ),
    )


def _panel_text(panel) -> str:
    """Extract the plain text from a Panel's renderable."""
    return panel.renderable.plain


class TestStatusGlyph:
    def test_healthy(self):
        assert _status_glyph(Status.HEALTHY) == ("✓", "green")

    def test_warning(self):
        assert _status_glyph(Status.WARNING) == ("⚠", "yellow")

    def test_critical(self):
        assert _status_glyph(Status.CRITICAL) == ("✗", "red")

    def test_unknown(self):
        assert _status_glyph(Status.UNKNOWN) == ("?", "dim")


class TestPanels:
    def test_recommendation(self, result, en_locale):
        panel = _render_recommendation(result)
        assert panel.title == "Recommendation"
        text = _panel_text(panel)
        assert "🟢" in text
        assert "Active project" in text
        assert "42 authors" in text
        assert "confidence: 100%" in text

    def test_release_health(self, result, en_locale):
        panel = _render_release_health(result)
        assert panel.title == "Release Health"
        text = _panel_text(panel)
        assert "v1.0.0" in text
        assert "age: 10 days" in text
        assert "cadence: 14 days/release" in text
        assert "semver: yes" in text
        assert "status: pre-release" in text

    def test_license(self, result, en_locale):
        panel = _render_license(result)
        assert panel.title == "License"
        assert "MIT (permissive, OSI)" in _panel_text(panel)

    def test_license_missing(self, result, en_locale):
        result.license.spdx_id = None
        panel = _render_license(result)
        assert "No license detected" in _panel_text(panel)

    def test_contributors(self, result, en_locale):
        panel = _render_contributors(result)
        assert panel.title == "Contributors"
        text = _panel_text(panel)
        assert "total: 42" in text
        assert "bus factor: 3" in text
        assert "bots: 20%" in text
        assert "lead: alice (100 commits)" in text
        assert "historical: bob" in text
        assert "minor: 5" in text
        assert "activity: 10 commits (3m)" in text

    def test_maintenance(self, result, en_locale):
        panel = _render_maintenance(result)
        assert panel.title == "Maintenance"
        text = _panel_text(panel)
        assert "state: active" in text
        assert "last commit: today" in text
        assert "frequency: 2.5 commits/month" in text
        assert "issues closed: 4d" in text

    def test_maintenance_abandoned(self, result, en_locale):
        result.maintenance.state = MaintenanceState.ABANDONED
        result.maintenance.last_commit_days_ago = 200
        panel = _render_maintenance(result)
        assert "state: abandoned" in _panel_text(panel)
        assert "last commit: 200d ago" in _panel_text(panel)

    def test_languages(self, result, en_locale):
        panel = _render_languages(result)
        assert panel.title == "Languages"
        text = _panel_text(panel)
        assert "primary: Python" in text
        assert "Python" in text
        assert "ecosystem: python" in text

    def test_sustainability(self, result, en_locale):
        panel = _render_sustainability(result)
        assert panel.title == "Sustainability"
        text = _panel_text(panel)
        assert "funding: GitHub Sponsors" in text
        assert "corporate: Acme Corp" in text
        assert "governance: Core team" in text

    def test_sustainability_no_backing(self, result, en_locale):
        result.sustainability.funding_platforms = []
        result.sustainability.corporate_backing = None
        panel = _render_sustainability(result)
        assert "no backing detected" in _panel_text(panel)

    def test_registries(self, result, en_locale):
        panel = _render_registries(result)
        assert panel is not None
        assert panel.title == "Package Registries"
        text = _panel_text(panel)
        assert "✓ mypkg @ 1.0" in text
        assert "downloads: 2,500,000" in text
        assert "license: MIT" in text
        assert "GitHub license: matches" in text
        assert "✗ other (not found)" in text

    def test_registries_none(self, result, en_locale):
        result.registries = []
        assert _render_registries(result) is None


class TestQualitativePanel:
    def test_panel_shows_signals(self, result, en_locale):
        result.qualitative = QualitativeIndicator(
            roadmap="v2 planned",
            commercial_support="paid tiers",
            security_policy="private advisory",
            text_maintenance_state="active",
            available=True,
            status=Status.HEALTHY,
        )
        panel = _render_qualitative(result)
        assert panel is not None
        assert panel.title == "Qualitative Signals"
        text = _panel_text(panel)
        assert "roadmap: v2 planned" in text
        assert "commercial support: paid tiers" in text
        assert "security: private advisory" in text
        assert "declared state: active" in text

    def test_panel_hidden_when_not_available(self, result, en_locale):
        assert _render_qualitative(result) is None


class TestLlmRecommendationPanel:
    def test_panel_shows_refined_recommendation(self, result, en_locale):
        result.llm_recommendation = LLMRecommendation(
            level="orange",
            message="Promising but young",
            explanation="Active development but a small community.",
            confidence=0.7,
        )
        panel = _render_llm_recommendation(result)
        assert panel is not None
        assert panel.title == "Refined recommendation (LLM)"
        text = _panel_text(panel)
        assert "🟠" in text
        assert "Promising but young" in text
        assert "Active development but a small community." in text
        assert "confidence: 70%" in text

    def test_panel_hidden_when_none(self, result, en_locale):
        assert _render_llm_recommendation(result) is None

    def test_panel_hidden_when_invalid_level(self, result, en_locale):
        result.llm_recommendation = LLMRecommendation(level="purple")
        assert _render_llm_recommendation(result) is None


class TestWarningsPanel:
    def test_panel_shows_warnings(self, result, en_locale):
        result.warnings = ["No GitHub token set", "LLM unavailable"]
        panel = _render_warnings(result)
        assert panel is not None
        assert panel.title == "Warnings"
        text = _panel_text(panel)
        assert "No GitHub token set" in text
        assert "LLM unavailable" in text

    def test_panel_hidden_when_no_warnings(self, result, en_locale):
        assert _render_warnings(result) is None

    def test_warnings_appear_in_dashboard(self, result, en_locale):
        result.warnings = ["No GitHub token set"]
        buf = io.StringIO()
        console = Console(file=buf, width=100, force_terminal=False)
        render_dashboard(result, console)
        output = buf.getvalue()
        assert "Warnings" in output
        assert "No GitHub token set" in output


class TestRenderDashboard:
    def _render(self, result, lang: str) -> str:
        buf = io.StringIO()
        console = Console(file=buf, width=100, force_terminal=False)
        with _locale(lang):
            render_dashboard(result, console)
        return buf.getvalue()

    def test_header_and_panels(self, result, en_locale):
        output = self._render(result, "en_US.UTF-8")
        assert "GitHub Health Dashboard - https://github.com/owner/repo" in output
        assert "A demo" in output
        assert "Stars: 1,234" in output
        assert "Owner: organization" in output
        for title in (
            "Recommendation",
            "Release Health",
            "License",
            "Contributors",
            "Maintenance",
            "Languages",
            "Sustainability",
            "Package Registries",
        ):
            assert title in output, f"missing panel title: {title}"

    def test_recommendation_prominent(self, result, en_locale):
        output = self._render(result, "en_US.UTF-8")
        # The verdict is displayed at the top
        assert "Active project" in output
        assert output.index("Active project") < output.index("Release Health")

    @pytest.mark.parametrize("lang", ["en_US.UTF-8", "fr_FR.UTF-8"])
    def test_localized_dashboard(self, result, monkeypatch, lang):
        monkeypatch.setenv("LANG", lang)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        buf = io.StringIO()
        console = Console(file=buf, width=100, force_terminal=False)
        render_dashboard(result, console)
        # The header must come from the catalog for the pinned language
        # (compare with t() so wording tweaks don't break the test).
        expected_header = t("tui_header", lang=lang.split("_")[0])
        assert expected_header in buf.getvalue()


class _locale:
    """Context manager pinning LANG for a render call."""

    def __init__(self, lang: str):
        self.lang = lang
        self.old = None

    def __enter__(self):
        import os

        self.old = os.environ.get("LANG")
        os.environ["LANG"] = self.lang
        os.environ.pop("LC_ALL", None)
        os.environ.pop("LC_MESSAGES", None)

    def __exit__(self, *exc):
        import os

        if self.old is None:
            os.environ.pop("LANG", None)
        else:
            os.environ["LANG"] = self.old
