"""Tests for the recommendation analyzer."""

from datetime import datetime, timedelta, timezone

from gh_score.core.analyzers.recommendation import analyze_recommendation
from gh_score.core.models import (
    AnalysisResult,
    ContributorsIndicator,
    LicenseIndicator,
    MaintenanceIndicator,
    MaintenanceState,
    Recommendation,
    RecommendationLevel,
    RegistryInfo,
    ReleaseHealthIndicator,
    RepoUrl,
    RepositoryMeta,
    Status,
    SustainabilityIndicator,
)


def _recommend(result: AnalysisResult) -> Recommendation:
    """Analyze with a fixed language so tests are locale-independent."""
    return analyze_recommendation(result, lang="fr")


def _make_result(
    *,
    state: MaintenanceState = MaintenanceState.UNKNOWN,
    stars: int = 0,
    forks: int = 0,
    archived: bool = False,
    disabled: bool = False,
    total_authors: int = 0,
    bot_ratio: float = 0.0,
    activity_trend: dict | None = None,
    latest_version: str | None = None,
    is_prerelease: bool = False,
    age_days: int | None = None,
    registries: list[RegistryInfo] | None = None,
    created_at: datetime | None = None,
    last_commit_days_ago: int | None = None,
    maintenance_status: Status = Status.HEALTHY,
    contributors_status: Status = Status.HEALTHY,
    release_status: Status = Status.HEALTHY,
    sustainability_status: Status = Status.HEALTHY,
) -> AnalysisResult:
    """Build an AnalysisResult with only the fields the recommendation
    analyzer reads, defaults for everything else."""
    now = datetime.now(timezone.utc)
    if created_at is None:
        created_at = now - timedelta(days=2 * 365)  # old enough to not be ephemeral

    meta = RepositoryMeta(
        stars=stars,
        forks=forks,
        archived=archived,
        disabled=disabled,
        created_at=created_at,
    )
    maintenance = MaintenanceIndicator(
        state=state,
        last_commit_days_ago=last_commit_days_ago,
        status=maintenance_status,
    )
    contributors = ContributorsIndicator(
        total_authors=total_authors,
        bot_ratio=bot_ratio,
        activity_trend=activity_trend or {},
        status=contributors_status,
    )
    release = ReleaseHealthIndicator(
        latest_version=latest_version,
        is_prerelease=is_prerelease,
        age_days=age_days,
        status=release_status,
    )
    sustainability = SustainabilityIndicator(status=sustainability_status)

    return AnalysisResult(
        url=RepoUrl("owner", "repo"),
        meta=meta,
        release_health=release,
        license=LicenseIndicator(),
        contributors=contributors,
        maintenance=maintenance,
        languages=None,  # type: ignore[arg-type]  # not read by recommendation
        sustainability=sustainability,
        registries=registries or [],
    )


class TestAbandoned:
    def test_abandoned_small_project_is_red(self):
        result = _make_result(
            state=MaintenanceState.ABANDONED,
            last_commit_days_ago=300,
            stars=50,
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.RED
        assert "abandonné" in rec.message
        assert "10 mois" in rec.message  # 300 days ≈ 10 months

    def test_abandoned_widely_used_is_orange(self):
        result = _make_result(
            state=MaintenanceState.ABANDONED,
            last_commit_days_ago=400,
            stars=20_000,
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert rec.message == "Grand projet, mais maintenant abandonné"

    def test_abandoned_with_high_downloads_is_orange(self):
        result = _make_result(
            state=MaintenanceState.ABANDONED,
            last_commit_days_ago=400,
            registries=[RegistryInfo(ecosystem="pypi", downloads=5_000_000)],
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE


class TestActive:
    def test_active_plain(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            latest_version="v1.0.0",
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.GREEN
        assert rec.message == "Projet actif"

    def test_active_large_community(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            total_authors=150,
            stars=2_000,
            latest_version="v1.0.0",
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.GREEN
        assert rec.message == "Projet actif avec une grande communauté"

    def test_active_with_many_stars_is_large_community(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=15_000,
            latest_version="v1.0.0",
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.GREEN
        assert "grande communauté" in rec.message

    def test_active_bot_dominated(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            bot_ratio=0.9,
            latest_version="v1.2.3",
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "bots" in rec.message

    def test_active_no_stable_release(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            latest_version=None,
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "pas encore stabilisé" in rec.message

    def test_active_prerelease_only(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            latest_version="v2.0.0-beta",
            is_prerelease=True,
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "pas encore stabilisé" in rec.message

    def test_active_pre_10_version(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            latest_version="v0.9.1",
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE

    def test_active_declining(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            latest_version="v1.0.0",
            activity_trend={"3m": 5, "12m": 120},
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "déclin" in rec.message


class TestMaintenance:
    def test_maintenance_with_old_release(self):
        result = _make_result(
            state=MaintenanceState.MAINTENANCE,
            latest_version="v1.0.0",
            age_days=240,  # 8 months
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "sans nouveautés depuis 8 mois" in rec.message

    def test_maintenance_plain(self):
        result = _make_result(
            state=MaintenanceState.MAINTENANCE,
            latest_version="v1.0.0",
            age_days=30,
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert rec.message == "Projet en mode maintenance"


class TestEphemeral:
    def test_young_tiny_project(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=10,
            total_authors=1,
            created_at=datetime.now(timezone.utc) - timedelta(days=20),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "éphémère" in rec.message

    def test_young_but_serious_project_is_not_ephemeral(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=5_000,
            total_authors=10,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        rec = _recommend(result)
        assert rec.message != "Projet éphémère accompagnant un article"


class TestHardFlags:
    def test_archived(self):
        result = _make_result(state=MaintenanceState.ACTIVE, archived=True)
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.RED
        assert "archivé" in rec.message

    def test_disabled(self):
        result = _make_result(state=MaintenanceState.ACTIVE, disabled=True)
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.RED
        assert "désactivé" in rec.message

    def test_registry_deprecated(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            registries=[RegistryInfo(ecosystem="pypi", exists=True, deprecated=True)],
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.RED
        assert "déprécié" in rec.message


class TestUnknown:
    def test_unknown_widely_used(self):
        result = _make_result(state=MaintenanceState.UNKNOWN, stars=8_000)
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "largement utilisé" in rec.message

    def test_unknown_insufficient_data(self):
        result = _make_result(state=MaintenanceState.UNKNOWN, stars=10)
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "insuffisantes" in rec.message


class TestMetadata:
    def test_confidence_is_high_with_full_data(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            total_authors=5,
            latest_version="v1.0.0",
        )
        rec = _recommend(result)
        assert rec.confidence == 1.0

    def test_confidence_lower_with_unknown_indicators(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            total_authors=5,
            latest_version="v1.0.0",
            maintenance_status=Status.UNKNOWN,
            contributors_status=Status.UNKNOWN,
            release_status=Status.UNKNOWN,
            sustainability_status=Status.UNKNOWN,
        )
        rec = _recommend(result)
        assert rec.confidence == 0.0

    def test_reasoning_includes_facts(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=1_234,
            total_authors=7,
            latest_version="v1.0.0",
        )
        rec = _recommend(result)
        assert any("1,234 étoiles" in r for r in rec.reasoning)
        assert any("7 auteurs" in r for r in rec.reasoning)
