"""Security updates analyzer.

Turns the raw open Dependabot security PRs into a status + localized
interpretation. A pending security update is normal Dependabot flow while
it is fresh; an update left open for several days means a known
vulnerability is being ignored.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gh_score.core.models import (
    Repository,
    SecurityIndicator,
    Status,
)
from gh_score.i18n import t

# A security update pending longer than this many days is treated as
# critical (a known vulnerability being ignored).
_PENDING_LIMIT_DAYS = 3


def analyze_security(
    repo: Repository,
    lang: str | None = None,
) -> SecurityIndicator:
    """Analyze pending security updates.

    Args:
        repo: Repository with raw open Dependabot security PRs.
        lang: Language for the interpretation.

    Returns a SecurityIndicator with:
    - Number of pending updates
    - Age in days of the oldest one
    - Status: HEALTHY (none), WARNING (recent), CRITICAL (overdue)
    - Interpretation
    """
    updates = repo.security_updates
    indicator = SecurityIndicator(updates=updates, pending_count=len(updates))

    if not updates:
        indicator.status = Status.HEALTHY
        indicator.interpretation = t("int_security_none", lang=lang)
        return indicator

    now = datetime.now(timezone.utc)
    ages = [
        (now - u.created_at).days
        for u in updates
        if u.created_at is not None
    ]
    if ages:
        indicator.oldest_days = max(ages)

    if indicator.oldest_days is not None and indicator.oldest_days >= _PENDING_LIMIT_DAYS:
        indicator.status = Status.CRITICAL
        indicator.interpretation = t(
            "int_security_overdue",
            lang=lang,
            count=len(updates),
            days=indicator.oldest_days,
        )
    else:
        indicator.status = Status.WARNING
        indicator.interpretation = t(
            "int_security_pending", lang=lang, count=len(updates)
        )

    return indicator
