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


def _detect_foundation(repo: Repository) -> str | None:
    """Detect if project is part of a recognized foundation."""
    # Check topics
    topics_lower = [t.lower() for t in repo.meta.topics]
    for key, name in _FOUNDATIONS.items():
        if key in topics_lower:
            return name

    # Check README and GOVERNANCE
    texts = [
        repo.readme_content or "",
        repo.governance_content or "",
    ]

    for text in texts:
        text_lower = text.lower()
        for key, name in _FOUNDATIONS.items():
            if key.replace("-", " ") in text_lower or name.lower() in text_lower:
                return name

    return None


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
      (_CORPORATE_KEYWORDS).
    """
    texts = [
        repo.readme_content or "",
        repo.governance_content or "",
    ]

    for text in texts:
        # Noun phrase: "OpenAI is the founding sponsor of the Warp repository".
        for pattern in _CORPORATE_SPONSOR_PATTERNS:
            match = pattern.search(text)
            if match:
                company = _clean_company(match.group(1))
                if company and not _is_self_mention(repo, company):
                    return company

        # Keyword phrase: "Backed by Acme Corp", "Sponsored by Acme Corp", …
        text_lower = text.lower()
        for keyword in _CORPORATE_KEYWORDS:
            if keyword in text_lower:
                # Try to extract company name (simple heuristic)
                # Look for patterns like "Backed by Company" or "Maintained by @company"
                pattern = rf"{keyword}\s+([A-Z][A-Za-z0-9\s]+)"
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    company = _clean_company(match.group(1))
                    if company and not _is_self_mention(repo, company):
                        return company

    return None


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
    foundation = _detect_foundation(repo)
    corporate_backing = _detect_corporate_backing(repo)
    governance_model = _detect_governance_model(repo)

    has_funding = len(funding_platforms) > 0 or hasattr(repo.community, "has_funding")

    indicator = SustainabilityIndicator(
        has_funding=has_funding,
        funding_platforms=funding_platforms,
        corporate_backing=corporate_backing,
        foundation=foundation,
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
