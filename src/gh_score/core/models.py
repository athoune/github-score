"""Data models for the GitHub health scorer.

All models use dataclasses for lightweight serialization.
They represent the raw data fetched from various sources,
before analysis into indicators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

_GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoUrl:
    """Parsed and validated GitHub repository URL."""

    owner: str
    repo: str

    @classmethod
    def parse(cls, url: str) -> RepoUrl:
        """Parse a GitHub URL into owner/repo.

        Raises ValueError if the URL is not a valid GitHub repository URL.
        """
        m = _GITHUB_URL_RE.match(url.strip())
        if not m:
            raise ValueError(f"Not a valid GitHub repository URL: {url!r}")
        return cls(owner=m.group("owner"), repo=m.group("repo"))

    @property
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    def __str__(self) -> str:
        return self.html_url


# ---------------------------------------------------------------------------
# License
# ---------------------------------------------------------------------------

class LicenseFamily(Enum):
    PERMISSIVE = "permissive"
    COPYLEFT = "copyleft"
    PUBLIC_DOMAIN = "public_domain"
    PROPRIETARY = "proprietary"
    OTHER = "other"


@dataclass
class LicenseInfo:
    spdx_id: str | None = None
    name: str | None = None
    osi_approved: bool = False
    family: LicenseFamily = LicenseFamily.OTHER
    detected_from_file: str | None = None  # fallback detection from LICENSE text


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------

@dataclass
class Release:
    tag_name: str
    name: str | None = None
    published_at: datetime | None = None
    prerelease: bool = False
    draft: bool = False
    html_url: str | None = None


@dataclass
class ReleaseHealth:
    """Raw release data for analysis."""
    releases: list[Release] = field(default_factory=list)

    @property
    def latest(self) -> Release | None:
        non_draft = [r for r in self.releases if not r.draft]
        if not non_draft:
            return None
        return max(non_draft, key=lambda r: r.published_at or datetime.min)


# ---------------------------------------------------------------------------
# Contributors
# ---------------------------------------------------------------------------

@dataclass
class Contributor:
    login: str
    avatar_url: str | None = None
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    is_bot: bool = False
    email_domain: str | None = None
    company: str | None = None


@dataclass
class ContributorStats:
    """Raw contributor data for analysis."""
    contributors: list[Contributor] = field(default_factory=list)
    total_commit_count: int = 0  # may differ from sum of contributor commits


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    number: int
    title: str
    state: str  # "open" or "closed"
    created_at: datetime
    closed_at: datetime | None = None
    is_pull_request: bool = False
    labels: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------

@dataclass
class Commit:
    sha: str
    author_login: str | None = None
    author_email: str | None = None
    author_date: datetime | None = None
    message: str = ""
    additions: int = 0
    deletions: int = 0


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------

@dataclass
class LanguageBreakdown:
    """Language breakdown from GitHub Linguist. Values are bytes."""
    languages: dict[str, int] = field(default_factory=dict)

    @property
    def primary(self) -> str | None:
        if not self.languages:
            return None
        return max(self.languages, key=self.languages.get)  # type: ignore[arg-type]

    @property
    def total_bytes(self) -> int:
        return sum(self.languages.values())

    def percentages(self) -> dict[str, float]:
        total = self.total_bytes
        if total == 0:
            return {}
        return {lang: (bytes_count / total) * 100 for lang, bytes_count in self.languages.items()}


# ---------------------------------------------------------------------------
# Repository metadata
# ---------------------------------------------------------------------------

@dataclass
class RepositoryMeta:
    """Core metadata from the GitHub API."""
    name: str = ""
    full_name: str = ""
    owner: str = ""
    owner_type: str = ""  # "user" | "organization" (GitHub API owner.type, normalized)
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None
    default_branch: str = "main"
    archived: bool = False
    disabled: bool = False
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues_count: int = 0
    topics: list[str] = field(default_factory=list)
    has_wiki: bool = False
    homepage: str | None = None
    size_kb: int = 0


# ---------------------------------------------------------------------------
# Community files
# ---------------------------------------------------------------------------

@dataclass
class CommunityFiles:
    """Presence of community/health files."""
    has_readme: bool = False
    has_license: bool = False
    has_contributing: bool = False
    has_code_of_conduct: bool = False
    has_security: bool = False
    has_governance: bool = False
    has_changelog: bool = False
    funding: dict[str, list[str]] = field(default_factory=dict)
    # e.g. {"github": ["user1"], "open_collective": ["project"]}


# ---------------------------------------------------------------------------
# Package registry
# ---------------------------------------------------------------------------

@dataclass
class RegistryInfo:
    """Information about a package on a registry."""
    ecosystem: str  # "pypi", "npm", "crates.io", "go", "maven", "rubygems", "docker"
    package_name: str | None = None
    exists: bool = False
    latest_version: str | None = None
    latest_date: datetime | None = None
    downloads: int | None = None  # approximate total downloads
    recent_downloads: int | None = None  # recent downloads (last 90 days or similar)
    deprecated: bool = False  # whether the package is marked deprecated
    registry_license: str | None = None  # license declared on the registry (SPDX)
    license_matches_github: bool | None = None  # comparison with GitHub-detected license
    is_heuristic: bool = False  # kept for backward compat; always False now


# ---------------------------------------------------------------------------
# Repository: aggregate of all raw data
# ---------------------------------------------------------------------------

@dataclass
class Repository:
    """Aggregate model containing all fetched data for a repository."""
    url: RepoUrl
    meta: RepositoryMeta = field(default_factory=RepositoryMeta)
    license: LicenseInfo = field(default_factory=LicenseInfo)
    release_health: ReleaseHealth = field(default_factory=ReleaseHealth)
    contributors: ContributorStats = field(default_factory=ContributorStats)
    issues: list[Issue] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    languages: LanguageBreakdown = field(default_factory=LanguageBreakdown)
    community: CommunityFiles = field(default_factory=CommunityFiles)
    registries: list[RegistryInfo] = field(default_factory=list)
    readme_content: str | None = None
    governance_content: str | None = None
    security_content: str | None = None
    llm_signals: dict[str, str | list[str] | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Indicator results (output of analyzers)
# ---------------------------------------------------------------------------

class Status(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class ReleaseHealthIndicator:
    latest_version: str | None = None
    latest_date: datetime | None = None
    age_days: int | None = None
    cadence_days: float | None = None  # avg days between releases (12m)
    semver_compliant: bool | None = None
    is_prerelease: bool = False
    status: Status = Status.UNKNOWN
    interpretation: str = ""


@dataclass
class LicenseIndicator:
    spdx_id: str | None = None
    family: LicenseFamily = LicenseFamily.OTHER
    osi_approved: bool = False
    status: Status = Status.UNKNOWN
    interpretation: str = ""


class ContributorArchetype(Enum):
    LEAD = "lead"                    # dominant over last 12 months
    HISTORICAL_LEAD = "historical_lead"  # dominant historically, no longer active
    MINOR = "minor"                  # few commits, drive-by


@dataclass
class ContributorDetail:
    login: str
    commits: int
    archetype: ContributorArchetype
    is_bot: bool = False


@dataclass
class ContributorsIndicator:
    total_authors: int = 0
    bus_factor: int = 0
    bot_ratio: float = 0.0  # 0.0 to 1.0
    lead: ContributorDetail | None = None
    historical_lead: ContributorDetail | None = None
    minor_count: int = 0
    activity_trend: dict[str, int] = field(default_factory=dict)
    # {"3m": N, "6m": N, "12m": N, "24m": N} commit counts
    status: Status = Status.UNKNOWN
    interpretation: str = ""


class MaintenanceState(Enum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    ABANDONED = "abandoned"
    UNKNOWN = "unknown"


@dataclass
class MaintenanceIndicator:
    last_commit_date: datetime | None = None
    last_commit_days_ago: int | None = None
    last_closed_date: datetime | None = None
    commits_per_month: float | None = None
    issue_velocity_days: float | None = None  # median time to close
    stale_issue_ratio: float | None = None
    state: MaintenanceState = MaintenanceState.UNKNOWN
    status: Status = Status.UNKNOWN
    interpretation: str = ""


@dataclass
class LanguagesIndicator:
    primary: str | None = None
    breakdown: dict[str, float] = field(default_factory=dict)  # percentages
    ecosystem: str | None = None  # inferred from manifest files
    interpretation: str = ""


@dataclass
class SustainabilityIndicator:
    has_funding: bool = False
    funding_platforms: list[str] = field(default_factory=list)
    corporate_backing: str | None = None
    foundation: str | None = None
    governance_model: str | None = None
    llm_signals: dict[str, str | list[str] | None] = field(default_factory=dict)
    # e.g. {"sponsors": ["Company X"], "roadmap": "...", "security_policy": "..."}
    status: Status = Status.UNKNOWN
    interpretation: str = ""


class RecommendationLevel(Enum):
    """Traffic-light verdict for the whole repository."""
    GREEN = "green"    # active project, safe to bet on
    ORANGE = "orange"  # potential but not stable, or widely used despite low maintenance
    RED = "red"        # risky: lack of maintenance


@dataclass
class Recommendation:
    """Synthesized recommendation crossing all indicator families."""
    level: RecommendationLevel = RecommendationLevel.ORANGE
    message: str = ""  # e.g. "Projet actif avec une grande communauté"
    confidence: float = 0.0  # 0.0 to 1.0, based on data completeness
    reasoning: list[str] = field(default_factory=list)  # human-readable reasons


# ---------------------------------------------------------------------------
# Analysis result: the full dashboard data
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """Complete analysis result for a repository."""
    url: RepoUrl
    meta: RepositoryMeta
    release_health: ReleaseHealthIndicator
    license: LicenseIndicator
    contributors: ContributorsIndicator
    maintenance: MaintenanceIndicator
    languages: LanguagesIndicator
    sustainability: SustainabilityIndicator
    registries: list[RegistryInfo] = field(default_factory=list)
    recommendation: Recommendation = field(default_factory=Recommendation)
