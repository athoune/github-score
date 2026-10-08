"""Sustainability analyzer.

Analyzes sustainability signals: funding, corporate backing, foundation membership.
"""

from __future__ import annotations

import re

from gh_score.core.models import (
    Repository,
    Status,
    SustainabilityIndicator,
)
from gh_score.core.text_context import (
    is_disclaimer_context,
    is_license_context,
    is_negated_before,
)
from gh_score.i18n import t

# Known foundations and organizations
_FOUNDATIONS = {
    "apache": "Apache Software Foundation",
    "cncf": "Cloud Native Computing Foundation",
    "linux-foundation": "Linux Foundation",
    "eclipse-foundation": "Eclipse Foundation",
    "mozilla-foundation": "Mozilla Foundation",
    "python-software-foundation": "Python Software Foundation",
    "fsf": "Free Software Foundation",
    "owasp": "OWASP Foundation",
}

# Prose patterns that assert a foundation *membership*. A bare short name
# ("apache") must never match: it also appears in license names ("Apache
# License", "Apache-2.0"), which are not relationships.
_FOUNDATION_TEXT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "apache": (
        re.compile(r"\bapache software foundation\b", re.IGNORECASE),
        re.compile(r"\bapache foundation\b", re.IGNORECASE),
        re.compile(r"\bapache incubator\b", re.IGNORECASE),
        re.compile(r"\basf\b", re.IGNORECASE),
    ),
    "cncf": (
        re.compile(r"\bcloud native computing foundation\b", re.IGNORECASE),
        re.compile(r"\bcncf\b", re.IGNORECASE),
    ),
    "linux-foundation": (re.compile(r"\blinux foundation\b", re.IGNORECASE),),
    "eclipse-foundation": (re.compile(r"\beclipse foundation\b", re.IGNORECASE),),
    "mozilla-foundation": (re.compile(r"\bmozilla foundation\b", re.IGNORECASE),),
    "python-software-foundation": (
        re.compile(r"\bpython software foundation\b", re.IGNORECASE),
        re.compile(r"\bpsf\b", re.IGNORECASE),
    ),
    "fsf": (
        re.compile(r"\bfree software foundation\b", re.IGNORECASE),
        re.compile(r"\bfsf\b", re.IGNORECASE),
    ),
    "owasp": (
        re.compile(r"\bowasp foundation\b", re.IGNORECASE),
        re.compile(r"\bowasp\b", re.IGNORECASE),
    ),
}

# Funding platform keywords
_FUNDING_PLATFORMS = {
    "github": "GitHub Sponsors",
    "open_collective": "Open Collective",
    "tidelift": "Tidelift",
    "patreon": "Patreon",
    "ko_fi": "Ko-fi",
    "liberapay": "Liberapay",
    "custom": "Custom funding",
}

# Corporate backing keywords: "<…> backed by <Company>" — the company
# name follows the keyword.
_CORPORATE_KEYWORDS = [
    "backed by",
    "sponsored by",
    "supported by",
    "maintained by",
    "developed by",
    "created by",
    "funded by",
]

# Corporate backing noun-phrase patterns: "<Company> is a/the (founding)
# sponsor of <…>" — the company name precedes the backing noun. Covers
# phrasings such as "OpenAI is the founding sponsor of the Warp repository".
# Not covered: past tense ("was a sponsor"), title-case verbs.
_CORPORATE_SPONSOR_PATTERNS = [
    re.compile(
        r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)*)"
        r"\s+(?:is|are)\s+"
        r"(?:a\s+|an\s+|the\s+)?"
        r"(?:founding\s+|primary\s+|proud\s+|lead\s+|major\s+|gold\s+|"
        r"platinum\s+|official\s+|corporate\s+)?"
        r"(?:sponsor|backer|supporter|funder)s?\s+of\b"
    ),
]


def _keyword_company_pattern(keyword: str) -> re.Pattern[str]:
    """Keyword-first pattern: the keyword is case-insensitive, the captured
    company name is not.

    Requiring an uppercase start prevents capturing coordination such as
    "sponsored by **or endorsed by** Adobe Inc." as a company name.
    """
    return re.compile(
        rf"\b(?i:{re.escape(keyword)})\b\s+"
        r"([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*)*)"
    )


_CORPORATE_KEYWORD_PATTERNS = {
    keyword: _keyword_company_pattern(keyword) for keyword in _CORPORATE_KEYWORDS
}


def _detect_funding_platforms(repo: Repository) -> list[str]:
    """Detect funding platforms from FUNDING.yml and README."""
    platforms = []

    # Check FUNDING.yml
    if hasattr(repo.community, "funding"):
        funding = repo.community.funding
        for platform in funding.keys():
            if platform in _FUNDING_PLATFORMS:
                platforms.append(_FUNDING_PLATFORMS[platform])

    # Check README for funding mentions
    if repo.readme_content:
        readme_lower = repo.readme_content.lower()
        if "github.com/sponsors" in readme_lower:
            if "GitHub Sponsors" not in platforms:
                platforms.append("GitHub Sponsors")
        if "opencollective.com" in readme_lower:
            if "Open Collective" not in platforms:
                platforms.append("Open Collective")
        if "patreon.com" in readme_lower:
            if "Patreon" not in platforms:
                platforms.append("Patreon")

    return platforms


def _detect_foundation(repo: Repository) -> tuple[str | None, str | None]:
    """Detect if project is part of a recognized foundation.

    Returns ``(name, source)`` where ``source`` is ``"owner"``, ``"topic"``
    or ``"text"``. Structured, self-declared signals (owner login, GitHub
    topic) win over prose; a prose match requires an explicit membership
    phrase and is rejected in license / disclaimer / negated context.
    """
    # 1. Owner login (a foundation org owns its projects).
    url_owner = repo.url.owner if repo.url else ""
    owner = (repo.meta.owner or url_owner or "").lower()
    if owner in _FOUNDATIONS:
        return _FOUNDATIONS[owner], "owner"

    # 2. GitHub topic, self-declared.
    topics_lower = {topic.lower() for topic in repo.meta.topics}
    for key, name in _FOUNDATIONS.items():
        if key in topics_lower:
            return name, "topic"

    # 3. Prose: only an explicit membership phrase, with context guards.
    texts = [
        repo.readme_content or "",
        repo.governance_content or "",
    ]

    for text in texts:
        for key, name in _FOUNDATIONS.items():
            for pattern in _FOUNDATION_TEXT_PATTERNS.get(key, ()):
                for match in pattern.finditer(text):
                    if is_license_context(text, match.start(), match.end()):
                        continue
                    if is_disclaimer_context(text, match.start(), match.end()):
                        continue
                    if is_negated_before(text, match.start()):
                        continue
                    return name, "text"

    return None, None


def _clean_company(raw: str) -> str | None:
    """Normalize a raw company match; None when it does not look like a name."""
    company = re.sub(r"[^\w\s]", "", raw).strip()
    if len(company) > 2 and len(company) < 50:
        return company
    return None


def _is_self_mention(repo: Repository, company: str) -> bool:
    """True when the sentence names the repository itself as the sponsor.

    "Warp is a sponsor of X" means Warp backs someone else — that is not
    backing of Warp. Compare against the owner/repo slug (URL and meta),
    case-insensitively.
    """
    slugs = {slug.lower() for slug in (repo.meta.name, repo.meta.owner) if slug}
    slugs.update(slug.lower() for slug in (repo.url.owner, repo.url.repo))
    return company.lower().strip() in slugs


def _detect_corporate_backing(repo: Repository) -> str | None:
    """Detect corporate backing from explicit mentions in README/GOVERNANCE.

    Two phrasings are recognized:
    - "<Company> is a/the (founding) sponsor of …" — the company precedes
      the backing noun (_CORPORATE_SPONSOR_PATTERNS);
    - "<…> backed by <Company>" — the company follows the keyword
      (_CORPORATE_KEYWORD_PATTERNS).

    Every match is rejected in negated ("not affiliated with, sponsored by
    or endorsed by Adobe Inc."), disclaimer / competitor ("trademarks of",
    "competitor", "alternative to") context.
    """
    texts = [
        repo.readme_content or "",
        repo.governance_content or "",
    ]

    for text in texts:
        # Noun phrase: "OpenAI is the founding sponsor of the Warp repository".
        for pattern in _CORPORATE_SPONSOR_PATTERNS:
            for match in pattern.finditer(text):
                if _backing_context_rejected(text, match):
                    continue
                company = _clean_company(match.group(1))
                if company and not _is_self_mention(repo, company):
                    return company

        # Keyword phrase: "Backed by Acme Corp", "Sponsored by Acme Corp", …
        for pattern in _CORPORATE_KEYWORD_PATTERNS.values():
            for match in pattern.finditer(text):
                if _backing_context_rejected(text, match):
                    continue
                company = _clean_company(match.group(1))
                if company and not _is_self_mention(repo, company):
                    return company

    return None


def _backing_context_rejected(text: str, match: re.Match[str]) -> bool:
    """True when a backing match sits in negated or disclaimer context."""
    return is_negated_before(text, match.start()) or is_disclaimer_context(
        text, match.start(), match.end()
    )


def _detect_governance_model(repo: Repository) -> str | None:
    """Detect governance model from GOVERNANCE file and README."""
    texts = [
        repo.governance_content or "",
        repo.readme_content or "",
    ]

    for text in texts:
        text_lower = text.lower()

        if "bdfl" in text_lower or "benevolent dictator" in text_lower:
            return "BDFL"
        if "core team" in text_lower or "maintainers team" in text_lower:
            return "Core team"
        if "steering committee" in text_lower:
            return "Steering committee"
        if "corporate-owned" in text_lower or "owned by" in text_lower:
            return "Corporate-owned"

    return None


def analyze_sustainability(
    repo: Repository,
    lang: str | None = None,
) -> SustainabilityIndicator:
    """Analyze sustainability signals from repository data.

    Args:
        repo: Repository with raw community data.
        lang: Language for the interpretation; defaults to the
            env-derived language.

    Returns a SustainabilityIndicator with:
    - Funding presence and platforms
    - Corporate backing
    - Foundation membership
    - Governance model
    - LLM-extracted signals (if available)
    - Status and interpretation
    """
    funding_platforms = _detect_funding_platforms(repo)
    foundation, foundation_source = _detect_foundation(repo)
    corporate_backing = _detect_corporate_backing(repo)
    governance_model = _detect_governance_model(repo)

    has_funding = len(funding_platforms) > 0 or hasattr(repo.community, "has_funding")

    indicator = SustainabilityIndicator(
        has_funding=has_funding,
        funding_platforms=funding_platforms,
        corporate_backing=corporate_backing,
        foundation=foundation,
        foundation_source=foundation_source,
        governance_model=governance_model,
    )

    indicator.status = _compute_status(indicator)
    indicator.interpretation = _build_interpretation(indicator, lang)

    return indicator


def _compute_status(ind: SustainabilityIndicator) -> Status:
    """Compute sustainability status."""
    # Strong signals
    if ind.foundation:
        return Status.HEALTHY

    if len(ind.funding_platforms) >= 2:
        return Status.HEALTHY

    if ind.corporate_backing and ind.has_funding:
        return Status.HEALTHY

    # Moderate signals
    if ind.has_funding or ind.corporate_backing:
        return Status.WARNING

    # No backing detected
    return Status.WARNING  # Not critical, just a risk factor


def _build_interpretation(
    ind: SustainabilityIndicator,
    lang: str | None = None,
) -> str:
    """Build human-readable interpretation."""
    parts = []

    if ind.foundation:
        if ind.foundation_source:
            parts.append(
                t(
                    "int_foundation_source",
                    lang=lang,
                    name=ind.foundation,
                    source=t(f"source_{ind.foundation_source}", lang=lang),
                )
            )
        else:
            parts.append(t("int_foundation", lang=lang, name=ind.foundation))

    if ind.funding_platforms:
        parts.append(
            t("int_funding", lang=lang, platforms=", ".join(ind.funding_platforms))
        )

    if ind.corporate_backing:
        parts.append(t("int_corporate", lang=lang, company=ind.corporate_backing))

    if ind.governance_model:
        parts.append(t("int_governance", lang=lang, model=ind.governance_model))

    if not parts:
        parts.append(t("int_no_backing", lang=lang))

    return ", ".join(parts)
