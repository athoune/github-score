"""Qualitative signals analyzer.

Maps the optional LLM-extracted facts (roadmap, security policy content,
commercial support, self-declared maintenance state) onto a typed
indicator. The indicator is ``available`` only when the LLM actually ran
and returned at least one signal; this drives confidence accounting and
gates the qualitative branches of the recommendation.
"""

from __future__ import annotations

from gh_score.core.models import (
    QualitativeIndicator,
    Repository,
    Status,
)
from gh_score.i18n import t


def analyze_qualitative(repo: Repository) -> QualitativeIndicator:
    """Build the QualitativeIndicator from repository LLM signals."""
    signals = repo.llm_signals
    available = bool(signals and signals.any)

    indicator = QualitativeIndicator(
        roadmap=signals.roadmap if available else None,
        security_policy=signals.security_policy if available else None,
        commercial_support=signals.commercial_support if available else None,
        text_maintenance_state=signals.text_maintenance_state if available else None,
        available=available,
        status=Status.HEALTHY if available else Status.UNKNOWN,
    )

    if available:
        indicator.interpretation = _build_interpretation(indicator)
    return indicator


def _build_interpretation(ind: QualitativeIndicator) -> str:
    parts = []
    if ind.roadmap:
        parts.append(t("int_roadmap", text=ind.roadmap))
    if ind.commercial_support:
        parts.append(t("int_commercial", text=ind.commercial_support))
    if ind.security_policy:
        parts.append(t("int_security", text=ind.security_policy))
    if ind.text_maintenance_state:
        parts.append(
            t("int_text_state", state=t(f"state_{ind.text_maintenance_state}"))
        )
    return ", ".join(parts)
