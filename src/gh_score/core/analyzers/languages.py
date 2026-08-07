"""Languages analyzer.

Analyzes language breakdown, ecosystem inference, and whether the main
language is mainstream or exotic. "Exotic" means the primary language is
not in the committed popularity datasets: the PYPL top-20
(``data/pypl_languages.csv``) nor the GitHub Innovation Graph top-20
(``data/github_languages.csv``). Refresh them with
``scripts/refresh_language_datasets.py``.
"""

from __future__ import annotations

import csv
import io
from functools import lru_cache
from importlib import resources

from gh_score.core.models import (
    LanguagesIndicator,
    Repository,
)
from gh_score.i18n import t

# Aliases between GitHub Linguist names and the dataset names: PYPL ranks
# "C/C++" while Linguist reports "C" and "C++" separately, etc.
_ALIASES: dict[str, frozenset[str]] = {
    "c/c++": frozenset({"c", "c++"}),
    "visual basic": frozenset({"vba", "visual basic"}),
    "delphi/pascal": frozenset({"delphi", "pascal", "object pascal"}),
}

# (dataset file, source label) pairs, both committed under data/.
_DATASETS: tuple[tuple[str, str], ...] = (
    ("pypl_languages.csv", "pypl"),
    ("github_languages.csv", "github"),
)


def analyze_languages(
    repo: Repository,
    lang: str | None = None,
) -> LanguagesIndicator:
    """Analyze language breakdown from repository data.

    Args:
        repo: Repository with raw language data.
        lang: Language for the interpretation; defaults to the
            env-derived language.

    Returns a LanguagesIndicator with:
    - Primary language
    - Full breakdown as percentages
    - Ecosystem inference
    - Popularity of the main language (mainstream vs exotic)
    - Interpretation
    """
    lang_breakdown = repo.languages

    indicator = LanguagesIndicator(
        primary=lang_breakdown.primary,
        breakdown=lang_breakdown.percentages(),
    )

    # Infer ecosystem from primary language or manifest files
    indicator.ecosystem = _infer_ecosystem(repo)
    _apply_popularity(indicator)
    indicator.interpretation = _build_interpretation(indicator, lang)

    return indicator


@lru_cache(maxsize=1)
def _load_rankings() -> dict[str, tuple[int, str]] | None:
    """Load ``language -> (best rank, source)`` from both datasets.

    Ranks are 1-based; the best (lowest) rank wins when a language appears
    in both sources. Returns None when the datasets cannot be read (broken
    install), in which case popularity is reported as unknown.
    """
    best: dict[str, tuple[int, str]] = {}
    try:
        for dataset, source in _DATASETS:
            text = (resources.files("gh_score") / "data" / dataset).read_text(
                encoding="utf-8"
            )
            for row in csv.DictReader(io.StringIO(text)):
                try:
                    rank = int(row["rank"])
                except (ValueError, KeyError):
                    continue
                name = row["language"].lower()
                for variant in {name} | _ALIASES.get(name, frozenset()):
                    prev = best.get(variant)
                    if prev is None or rank < prev[0]:
                        best[variant] = (rank, source)
    except (OSError, KeyError, csv.Error):
        return None
    return best


def _apply_popularity(indicator: LanguagesIndicator) -> None:
    """Set is_exotic / popularity_rank / popularity_source on the indicator."""
    primary = indicator.primary
    if not primary:
        return
    rankings = _load_rankings()
    if rankings is None:
        return
    entry = rankings.get(primary.lower())
    if entry is None:
        indicator.is_exotic = True
        return
    indicator.is_exotic = False
    indicator.popularity_rank, indicator.popularity_source = entry


def _infer_ecosystem(repo: Repository) -> str | None:
    """Infer ecosystem from languages and community files."""
    # Map of file checks to ecosystems
    # (We don't have direct access to file list, but we can infer from languages)
    primary = repo.languages.primary

    if primary:
        primary_lower = primary.lower()
        if primary_lower == "python":
            return "python"
        if primary_lower in ("javascript", "typescript"):
            return "javascript"
        if primary_lower == "rust":
            return "rust"
        if primary_lower == "go":
            return "go"
        if primary_lower == "java":
            return "java"
        if primary_lower == "ruby":
            return "ruby"

    return None


def _build_interpretation(
    ind: LanguagesIndicator,
    lang: str | None = None,
) -> str:
    """Build human-readable interpretation."""
    parts = []

    if ind.primary:
        parts.append(t("int_primary", lang=lang, language=ind.primary))

        if ind.is_exotic is True:
            parts.append(t("int_language_exotic", lang=lang, language=ind.primary))
        elif ind.is_exotic is False and ind.popularity_rank is not None:
            parts.append(
                t("int_language_popular", lang=lang, rank=ind.popularity_rank)
            )

    # Top 3 languages
    if ind.breakdown:
        sorted_langs = sorted(
            ind.breakdown.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:3]

        lang_strs = [f"{lang} ({pct:.0f}%)" for lang, pct in sorted_langs]
        parts.append(t("int_breakdown", lang=lang, langs=", ".join(lang_strs)))

    if ind.ecosystem:
        parts.append(t("int_ecosystem", lang=lang, ecosystem=ind.ecosystem))

    return ", ".join(parts) if parts else t("int_no_language", lang=lang)
