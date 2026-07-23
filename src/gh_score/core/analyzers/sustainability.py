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

# Corporate backing keywords
_CORPORATE_KEYWORDS = [
    "backed by", "sponsored by", "supported by", "maintained by",
    "developed by", "created by", "funded by",
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


def _detect_corporate_backing(repo: Repository) -> str | None:
    """Detect corporate backing from README and contributor affiliations."""
    texts = [
        repo.readme_content or "",
        repo.governance_content or "",
    ]

    for text in texts:
        text_lower = text.lower()
        for keyword in _CORPORATE_KEYWORDS:
            if keyword in text_lower:
                # Try to extract company name (simple heuristic)
                # Look for patterns like "Backed by Company" or "Maintained by @company"
                pattern = rf"{keyword}\s+([A-Z][A-Za-z0-9\s]+)"
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    company = match.group(1).strip()
                    # Clean up
                    company = re.sub(r"[^\w\s]", "", company)
                    if len(company) > 2 and len(company) < 50:
                        return company

    # Check contributor email domains
    major_contributors = repo.contributors.contributors[:5]
    domains = {}
    for c in major_contributors:
        if c.email_domain and not _is_masked_or_free_email(c.email_domain):
            domains[c.email_domain] = domains.get(c.email_domain, 0) + c.commits

    if domains:
        top_domain = max(domains, key=domains.get)  # type: ignore[arg-type]
        return top_domain

    return None


def _is_masked_or_free_email(domain: str) -> bool:
    """Check if email domain is a free provider or a masked/protected domain.

    Masked domains (like users.noreply.github.com) are used by GitHub
    and other platforms to protect contributor privacy. They must not
    be treated as corporate affiliations.
    """
    domain_lower = domain.lower()
    free_domains = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "protonmail.com", "icloud.com", "mail.com",
    }
    # GitHub masked/protected email domains
    masked_domains = {
        "users.noreply.github.com",
        "noreply.github.com",
        "github.com",
    }
    return domain_lower in free_domains or domain_lower in masked_domains


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


def analyze_sustainability(repo: Repository) -> SustainabilityIndicator:
    """Analyze sustainability signals from repository data.

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

    # Pick up LLM signals if available
    llm_signals: dict[str, str | list[str] | None] = repo.llm_signals

    indicator = SustainabilityIndicator(
        has_funding=has_funding,
        funding_platforms=funding_platforms,
        corporate_backing=corporate_backing,
        foundation=foundation,
        governance_model=governance_model,
        llm_signals=llm_signals,
    )

    indicator.status = _compute_status(indicator)
    indicator.interpretation = _build_interpretation(indicator)

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


def _build_interpretation(ind: SustainabilityIndicator) -> str:
    """Build human-readable interpretation."""
    parts = []

    if ind.foundation:
        parts.append(f"foundation: {ind.foundation}")

    if ind.funding_platforms:
        parts.append(f"funding: {', '.join(ind.funding_platforms)}")

    if ind.corporate_backing:
        parts.append(f"corporate: {ind.corporate_backing}")

    if ind.governance_model:
        parts.append(f"governance: {ind.governance_model}")

    if not parts:
        parts.append("no backing detected")

    return ", ".join(parts)
