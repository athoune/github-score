"""Release health analyzer.

Analyzes release patterns, cadence, and stability.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from gh_score.core.models import (
    ReleaseHealth,
    ReleaseHealthIndicator,
    Repository,
    Status,
)


# Semver pattern: MAJOR.MINOR.PATCH with optional pre-release
_SEMVER_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9.]+)?(?:\+[a-zA-Z0-9.]+)?$"
)


def _is_semver(tag: str) -> bool:
    """Check if a tag follows semantic versioning."""
    return bool(_SEMVER_RE.match(tag))


def analyze_release_health(repo: Repository) -> ReleaseHealthIndicator:
    """Analyze release health from repository data.

    Returns a ReleaseHealthIndicator with:
    - Latest version and date
    - Age in days
    - Release cadence (avg days between releases over 12 months)
    - Semver compliance
    - Pre-release status
    - Overall status and interpretation
    """
    rh = repo.release_health
    indicator = ReleaseHealthIndicator()

    if not rh.releases:
        indicator.status = Status.CRITICAL
        indicator.interpretation = "No releases found"
        return indicator

    latest = rh.latest
    if not latest:
        indicator.status = Status.CRITICAL
        indicator.interpretation = "No non-draft releases found"
        return indicator

    indicator.latest_version = latest.tag_name
    indicator.latest_date = latest.published_at
    indicator.is_prerelease = latest.prerelease

    # Age
    now = datetime.now(timezone.utc)
    if latest.published_at:
        indicator.age_days = (now - latest.published_at).days

    # Semver compliance: check all non-draft releases
    non_draft = [r for r in rh.releases if not r.draft]
    if non_draft:
        semver_count = sum(1 for r in non_draft if _is_semver(r.tag_name))
        indicator.semver_compliant = semver_count == len(non_draft)

    # Release cadence over last 12 months
    twelve_months_ago = now - __import__("datetime").timedelta(days=365)
    recent = [
        r for r in non_draft
        if r.published_at and r.published_at >= twelve_months_ago
    ]
    if len(recent) >= 2:
        sorted_recent = sorted(recent, key=lambda r: r.published_at)  # type: ignore[arg-type]
        dates = [r.published_at for r in sorted_recent]
        total_days = (dates[-1] - dates[0]).total_seconds() / 86400
        if total_days > 0:
            indicator.cadence_days = total_days / (len(dates) - 1)

    # Determine status
    indicator.status = _compute_status(indicator)
    indicator.interpretation = _build_interpretation(indicator)

    return indicator


def _compute_status(ind: ReleaseHealthIndicator) -> Status:
    """Compute overall status from release health indicators."""
    # No releases at all
    if ind.latest_version is None:
        return Status.CRITICAL

    # Very old release
    if ind.age_days is not None:
        if ind.age_days > 365:
            return Status.CRITICAL
        if ind.age_days > 180:
            return Status.WARNING

    # Pre-release as latest
    if ind.is_prerelease:
        return Status.WARNING

    # No recent releases (cadence unknown and age > 90 days)
    if ind.cadence_days is None and ind.age_days is not None and ind.age_days > 90:
        return Status.WARNING

    return Status.HEALTHY


def _build_interpretation(ind: ReleaseHealthIndicator) -> str:
    """Build a human-readable interpretation."""
    parts = []

    if ind.latest_version:
        parts.append(f"Latest: {ind.latest_version}")
        if ind.age_days is not None:
            if ind.age_days == 0:
                parts.append("released today")
            elif ind.age_days == 1:
                parts.append("released yesterday")
            else:
                parts.append(f"released {ind.age_days} days ago")

    if ind.cadence_days is not None:
        if ind.cadence_days < 7:
            parts.append(f"cadence: ~{ind.cadence_days:.0f}d/release (very active)")
        elif ind.cadence_days < 30:
            parts.append(f"cadence: ~{ind.cadence_days:.0f}d/release (active)")
        elif ind.cadence_days < 90:
            parts.append(f"cadence: ~{ind.cadence_days:.0f}d/release (moderate)")
        else:
            parts.append(f"cadence: ~{ind.cadence_days:.0f}d/release (slow)")

    if ind.semver_compliant is not None:
        if ind.semver_compliant:
            parts.append("semver: yes")
        else:
            parts.append("semver: no")

    if ind.is_prerelease:
        parts.append("pre-release")

    return ", ".join(parts) if parts else "No release data"
