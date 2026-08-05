"""Recommendation analyzer.

Cross-cuts all indicator families (maintenance, contributors, release
health, sustainability, registries, metadata) to produce a single
traffic-light verdict: green / orange / red, a contextual message, the
confidence based on data completeness, and the reasoning behind it.

Messages are localized through :mod:`gh_score.i18n`; the language comes
from the environment ($LANG / LC_ALL / LC_MESSAGES) unless one is passed
explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gh_score.core.models import (
    AnalysisResult,
    MaintenanceState,
    Recommendation,
    RecommendationLevel,
    Status,
)
from gh_score.i18n import current_language, t

# ---------------------------------------------------------------------------
# Thresholds (heuristics to tune with real-world examples)
# ---------------------------------------------------------------------------

_WIDELY_USED_STARS = 5_000
_WIDELY_USED_FORKS = 1_000
_WIDELY_USED_DOWNLOADS = 1_000_000
_LARGE_COMMUNITY_AUTHORS = 100
_LARGE_COMMUNITY_STARS = 10_000
_BOT_DOMINATED_RATIO = 0.8
_DECLINING_FACTOR = 0.25  # 3m commits < 25% of 12m commits → declining
_NO_RELEASE_MONTHS = 6    # maintained but no release for this long
_EPHEMERAL_AGE_DAYS = 180
_EPHEMERAL_MAX_AUTHORS = 3
_EPHEMERAL_MAX_STARS = 200


def _is_widely_used(result: AnalysisResult) -> bool:
    """High adoption: used by many, even if few people contribute."""
    meta = result.meta
    if meta.stars >= _WIDELY_USED_STARS or meta.forks >= _WIDELY_USED_FORKS:
        return True
    return any(
        (reg.downloads or 0) >= _WIDELY_USED_DOWNLOADS
        for reg in result.registries
    )


def _has_large_community(result: AnalysisResult) -> bool:
    """Many distinct human authors, or a very large audience."""
    return (
        result.contributors.total_authors >= _LARGE_COMMUNITY_AUTHORS
        or result.meta.stars >= _LARGE_COMMUNITY_STARS
    )


def _is_bot_dominated(result: AnalysisResult) -> bool:
    """Most commits come from dependency-update bots."""
    return result.contributors.bot_ratio >= _BOT_DOMINATED_RATIO


def _has_stable_release(result: AnalysisResult) -> bool:
    """A stable (non pre-release, >= 1.0) release exists."""
    rh = result.release_health
    if rh.latest_version is None or rh.is_prerelease:
        return False
    version = rh.latest_version.lstrip("vV")
    if version and version.split(".")[0] == "0":
        return False
    return True


def _is_declining(result: AnalysisResult) -> bool:
    """Recent (3m) commit activity well below the 12m average."""
    trend = result.contributors.activity_trend
    commits_3m = trend.get("3m", 0)
    commits_12m = trend.get("12m", 0)
    if commits_12m <= 0:
        return False
    return commits_3m < commits_12m * _DECLINING_FACTOR


def _is_ephemeral(result: AnalysisResult) -> bool:
    """Young, tiny, few authors: typical of an article/blog demo project."""
    meta = result.meta
    if meta.created_at is None:
        return False
    age_days = (datetime.now(timezone.utc) - meta.created_at).days
    if age_days > _EPHEMERAL_AGE_DAYS:
        return False
    if meta.stars > _EPHEMERAL_MAX_STARS:
        return False
    if result.contributors.total_authors > _EPHEMERAL_MAX_AUTHORS:
        return False
    return True


def _compute_confidence(result: AnalysisResult) -> float:
    """Confidence from data completeness: fraction of indicators with a
    known status (not UNKNOWN)."""
    known = 0
    total = 0
    for indicator in (
        result.maintenance,
        result.contributors,
        result.release_health,
        result.sustainability,
    ):
        total += 1
        if indicator.status != Status.UNKNOWN:
            known += 1
    if total == 0:
        return 0.0
    return round(known / total, 2)


def _build(
    level: RecommendationLevel,
    message: str,
    result: AnalysisResult,
    lang: str,
    *reasons: str,
) -> Recommendation:
    """Assemble the Recommendation with objective facts appended."""
    reasoning = [r for r in reasons if r]
    if result.meta.stars:
        reasoning.append(t("fact_stars", lang=lang, stars=result.meta.stars))
    if result.contributors.total_authors:
        reasoning.append(
            t("fact_authors", lang=lang, authors=result.contributors.total_authors)
        )
    return Recommendation(
        level=level,
        message=message,
        confidence=_compute_confidence(result),
        reasoning=reasoning,
    )


def analyze_recommendation(
    result: AnalysisResult,
    lang: str | None = None,
) -> Recommendation:
    """Synthesize all indicator families into a traffic-light verdict.

    Decision order matters: the strongest signals win first.

    Args:
        result: Full analysis result.
        lang: Language code for messages; defaults to the env-derived
            language ($LANG / LC_ALL / LC_MESSAGES).

    Returns:
        Recommendation with level, message, confidence and reasoning.
    """
    if lang is None:
        lang = current_language()

    maint = result.maintenance

    # 1. Hard red flags: no development possible anymore.
    if result.meta.archived:
        return _build(
            RecommendationLevel.RED,
            t("rec_archived", lang=lang),
            result,
            lang,
            t("reason_archived", lang=lang),
        )
    if result.meta.disabled:
        return _build(
            RecommendationLevel.RED,
            t("rec_disabled", lang=lang),
            result,
            lang,
            t("reason_disabled", lang=lang),
        )
    if any(reg.deprecated for reg in result.registries):
        return _build(
            RecommendationLevel.RED,
            t("rec_deprecated", lang=lang),
            result,
            lang,
            t("reason_deprecated", lang=lang),
        )

    # 2. Ephemeral project (checked before activity: a young project can
    #    still look "active" while being a weekend throwaway).
    if _is_ephemeral(result):
        return _build(
            RecommendationLevel.ORANGE,
            t("rec_ephemeral", lang=lang),
            result,
            lang,
            t("reason_ephemeral", lang=lang),
        )

    # 3. Abandoned.
    if maint.state == MaintenanceState.ABANDONED:
        if _is_widely_used(result):
            return _build(
                RecommendationLevel.ORANGE,
                t("rec_abandoned_popular", lang=lang),
                result,
                lang,
                t("reason_abandoned_popular", lang=lang),
            )
        n = maint.last_commit_days_ago or 0
        months = max(1, round(n / 30))
        return _build(
            RecommendationLevel.RED,
            t("rec_abandoned_months", lang=lang, months=months),
            result,
            lang,
            t("reason_last_commit_days", lang=lang, days=n),
        )

    # 4. Active development.
    if maint.state == MaintenanceState.ACTIVE:
        if _is_bot_dominated(result):
            return _build(
                RecommendationLevel.ORANGE,
                t("rec_bots", lang=lang),
                result,
                lang,
                t("reason_bots", lang=lang, ratio=result.contributors.bot_ratio),
            )
        if not _has_stable_release(result):
            return _build(
                RecommendationLevel.ORANGE,
                t("rec_not_stable", lang=lang),
                result,
                lang,
                t("reason_no_stable_release", lang=lang),
            )
        if _is_declining(result):
            return _build(
                RecommendationLevel.ORANGE,
                t("rec_declining", lang=lang),
                result,
                lang,
                t("reason_declining", lang=lang),
            )
        if _has_large_community(result):
            return _build(
                RecommendationLevel.GREEN,
                t("rec_active_community", lang=lang),
                result,
                lang,
                t("reason_active", lang=lang),
            )
        return _build(
            RecommendationLevel.GREEN,
            t("rec_active", lang=lang),
            result,
            lang,
            t("reason_active", lang=lang),
        )

    # 5. Maintenance mode: fixes without new features.
    if maint.state == MaintenanceState.MAINTENANCE:
        rh = result.release_health
        if rh.age_days is not None and rh.age_days > _NO_RELEASE_MONTHS * 30:
            n = max(1, round(rh.age_days / 30))
            return _build(
                RecommendationLevel.ORANGE,
                t("rec_maintenance_no_release", lang=lang, months=n),
                result,
                lang,
                t("reason_release_age_days", lang=lang, days=rh.age_days),
            )
        return _build(
            RecommendationLevel.ORANGE,
            t("rec_maintenance", lang=lang),
            result,
            lang,
            t("reason_maintenance", lang=lang),
        )

    # 6. Unknown maintenance state.
    if _is_widely_used(result):
        return _build(
            RecommendationLevel.ORANGE,
            t("rec_widely_used_unmaintained", lang=lang),
            result,
            lang,
            t("reason_unknown_widely_used", lang=lang),
        )
    return _build(
        RecommendationLevel.ORANGE,
        t("rec_insufficient_data", lang=lang),
        result,
        lang,
        t("reason_insufficient", lang=lang),
    )
