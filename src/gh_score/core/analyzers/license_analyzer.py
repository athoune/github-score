"""License analyzer.

Analyzes license information and classifies it.
"""

from __future__ import annotations

from gh_score.core.models import (
    LicenseFamily,
    LicenseIndicator,
    Repository,
    Status,
)


def analyze_license(repo: Repository) -> LicenseIndicator:
    """Analyze license from repository data.

    Returns a LicenseIndicator with:
    - SPDX ID
    - License family (permissive, copyleft, public domain, proprietary)
    - OSI approved flag
    - Status and interpretation
    """
    lic = repo.license
    indicator = LicenseIndicator(
        spdx_id=lic.spdx_id,
        family=lic.family,
        osi_approved=lic.osi_approved,
    )

    indicator.status = _compute_status(indicator)
    indicator.interpretation = _build_interpretation(indicator)

    return indicator


def _compute_status(ind: LicenseIndicator) -> Status:
    """Compute license status."""
    # No license at all
    if not ind.spdx_id:
        return Status.CRITICAL

    # OSI approved is good
    if ind.osi_approved:
        return Status.HEALTHY

    # Known families are acceptable
    if ind.family in (
        LicenseFamily.PERMISSIVE,
        LicenseFamily.COPYLEFT,
        LicenseFamily.PUBLIC_DOMAIN,
    ):
        return Status.HEALTHY

    # Unknown or proprietary
    if ind.family == LicenseFamily.PROPRIETARY:
        return Status.WARNING

    return Status.WARNING


def _build_interpretation(ind: LicenseIndicator) -> str:
    """Build human-readable interpretation."""
    parts = []

    if ind.spdx_id:
        parts.append(ind.spdx_id)

        # Family description
        family_desc = {
            LicenseFamily.PERMISSIVE: "permissive",
            LicenseFamily.COPYLEFT: "copyleft",
            LicenseFamily.PUBLIC_DOMAIN: "public domain",
            LicenseFamily.PROPRIETARY: "proprietary",
            LicenseFamily.OTHER: "other",
        }
        parts.append(family_desc.get(ind.family, "unknown"))

        if ind.osi_approved:
            parts.append("OSI-approved")
    else:
        parts.append("No license detected")

    return " (" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0]
