"""Languages analyzer.

Analyzes language breakdown and ecosystem inference.
"""

from __future__ import annotations

from gh_score.core.models import (
    LanguagesIndicator,
    Repository,
)


def analyze_languages(repo: Repository) -> LanguagesIndicator:
    """Analyze language breakdown from repository data.

    Returns a LanguagesIndicator with:
    - Primary language
    - Full breakdown as percentages
    - Ecosystem inference
    - Interpretation
    """
    lang_breakdown = repo.languages

    indicator = LanguagesIndicator(
        primary=lang_breakdown.primary,
        breakdown=lang_breakdown.percentages(),
    )

    # Infer ecosystem from primary language or manifest files
    indicator.ecosystem = _infer_ecosystem(repo)
    indicator.interpretation = _build_interpretation(indicator)

    return indicator


def _infer_ecosystem(repo: Repository) -> str | None:
    """Infer ecosystem from languages and community files."""
    # Check community files for manifest presence
    community = repo.community

    # Map of file checks to ecosystems
    # (We don't have direct access to file list, but we can infer from languages)
    primary = repo.languages.primary

    if primary:
        primary_lower = primary.lower()
        if primary_lower == "python":
            return "python"
        elif primary_lower in ("javascript", "typescript"):
            return "javascript"
        elif primary_lower == "rust":
            return "rust"
        elif primary_lower == "go":
            return "go"
        elif primary_lower == "java":
            return "java"
        elif primary_lower == "ruby":
            return "ruby"

    return None


def _build_interpretation(ind: LanguagesIndicator) -> str:
    """Build human-readable interpretation."""
    parts = []

    if ind.primary:
        parts.append(f"primary: {ind.primary}")

    # Top 3 languages
    if ind.breakdown:
        sorted_langs = sorted(
            ind.breakdown.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:3]

        lang_strs = [f"{lang} ({pct:.0f}%)" for lang, pct in sorted_langs]
        parts.append("breakdown: " + ", ".join(lang_strs))

    if ind.ecosystem:
        parts.append(f"ecosystem: {ind.ecosystem}")

    return ", ".join(parts) if parts else "No language data"
