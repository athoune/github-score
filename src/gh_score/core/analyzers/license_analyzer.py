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
from gh_score.i18n import t


def analyze_license(repo: Repository, lang: str | None = None) -> LicenseIndicator:
    """Analyze license from repository data.

    Args:
        repo: Repository with raw license data.
        lang: Language for the interpretation; defaults to the
            env-derived language.

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
    indicator.interpretation = _build_interpretation(indicator, lang)

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


_FAMILY_KEYS = {
    LicenseFamily.PERMISSIVE: "int_lic_family_permissive",
    LicenseFamily.COPYLEFT: "int_lic_family_copyleft",
    LicenseFamily.PUBLIC_DOMAIN: "int_lic_family_public_domain",
    LicenseFamily.PROPRIETARY: "int_lic_family_proprietary",
    LicenseFamily.OTHER: "int_lic_family_other",
}


def license_family_label(family: LicenseFamily, lang: str | None = None) -> str:
    """Localized display label for a license family."""
    key = _FAMILY_KEYS.get(family, "int_lic_family_unknown")
    return t(key, lang=lang)


def _build_interpretation(ind: LicenseIndicator, lang: str | None = None) -> str:
    """Build human-readable interpretation."""
    parts = []

    if ind.spdx_id:
        parts.append(ind.spdx_id)

        # Family description
        parts.append(license_family_label(ind.family, lang))

        if ind.osi_approved:
            parts.append(t("int_lic_osi_approved", lang=lang))
    else:
        parts.append(t("int_lic_none", lang=lang))

    return " (" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0]
