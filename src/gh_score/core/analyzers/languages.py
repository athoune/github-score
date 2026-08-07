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
import re
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
    indicator.readme_is_english = detect_readme_language(repo.readme_content)
    _apply_popularity(indicator)
    indicator.interpretation = _build_interpretation(indicator, lang)

    return indicator


# ---------------------------------------------------------------------------
# README language detection (dependency-free heuristic)
# ---------------------------------------------------------------------------

# English function words; their density is the "is this English?" signal.
_ENGLISH_STOPWORDS = frozenset({
    "the", "and", "of", "to", "for", "is", "in", "on", "with", "that",
    "this", "from", "by", "as", "at", "are", "it", "or", "an", "be",
    "you", "your", "can", "not", "has", "have",
})

# Minimum stopword share over the word sample to call the text English.
_ENGLISH_STOPWORD_RATIO = 0.12

# Below this many words the sample is too small to judge.
_MIN_README_WORDS = 20

# Non-Latin scripts: a sample dominated by them is definitely not English.
_NON_LATIN_RE = re.compile(
    "[\u0370-\u03FF"    # Greek
    "\u0400-\u04FF"     # Cyrillic
    "\u0590-\u05FF"     # Hebrew
    "\u0600-\u06FF"     # Arabic
    "\u0900-\u097F"     # Devanagari
    "\u0E00-\u0E7F"     # Thai
    "\u3040-\u30FF"     # Hiragana / Katakana
    "\u4E00-\u9FFF"     # CJK ideographs
    "]"
)

# A sample with more than this share of non-Latin characters is not English.
_NON_LATIN_MAX_SHARE = 0.05


def _extract_readme_sample(text: str, max_chars: int = 4000) -> str:
    """Strip markdown/URLs/code blocks and keep the leading prose."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)  # fenced code
    text = re.sub(r"`[^`]*`", " ", text)                      # inline code
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)      # links → label
    text = re.sub(r"https?://\S+", " ", text)                 # bare URLs
    text = re.sub(r"[#>*_\-|~]", " ", text)                   # markdown marks
    return text[:max_chars]


def detect_readme_language(text: str | None) -> bool | None:
    """True when the README looks like English, False when it clearly is
    not, None when there is no usable sample (no README or too little
    prose). Heuristic: dominant script + English stopword density."""
    if not text:
        return None
    sample = _extract_readme_sample(text)
    # A sample dominated by a non-Latin script is definitely not English,
    # whatever its Latin word count (a CJK README has almost none).
    if len(_NON_LATIN_RE.findall(sample)) > len(sample) * _NON_LATIN_MAX_SHARE:
        return False
    words = re.findall(r"[a-zà-ÿ]+", sample.lower())
    if len(words) < _MIN_README_WORDS:
        return None
    stopwords = sum(1 for w in words if w in _ENGLISH_STOPWORDS)
    return stopwords / len(words) >= _ENGLISH_STOPWORD_RATIO


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

    if ind.readme_is_english is True:
        parts.append(t("int_readme_english", lang=lang))
    elif ind.readme_is_english is False:
        parts.append(t("int_readme_not_english", lang=lang))

    return ", ".join(parts) if parts else t("int_no_language", lang=lang)
