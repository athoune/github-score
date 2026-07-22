"""Contributors analyzer.

Analyzes contributor patterns: bus factor, lead detection, bot ratio, activity trends.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gh_score.core.models import (
    Contributor,
    ContributorArchetype,
    ContributorDetail,
    ContributorsIndicator,
    Repository,
    Status,
)


# Known bot patterns
_BOT_PATTERNS = frozenset({
    "dependabot", "renovate", "github-actions", "greenkeeper",
    "snyk-bot", "codecov", "allcontributors", "imgbot",
    "stale", "mergify", "pre-commit-ci",
})


def _is_bot(login: str) -> bool:
    """Check if a login is a known bot."""
    login_lower = login.lower()
    return (
        login_lower in _BOT_PATTERNS
        or login_lower.endswith("[bot]")
        or "bot" in login_lower
    )


def _contributor_is_bot(c: Contributor) -> bool:
    """Check if a contributor is a bot, combining explicit flag and heuristic."""
    return c.is_bot or _is_bot(c.login)


def _compute_bus_factor(contributors: list[Contributor], threshold: float = 0.5) -> int:
    """Compute bus factor: smallest N contributors accounting for threshold% of commits.

    Args:
        contributors: List of contributors sorted by commits (descending)
        threshold: Percentage threshold (default 0.5 = 50%)

    Returns:
        Number of contributors needed to reach the threshold
    """
    if not contributors:
        return 0

    total_commits = sum(c.commits for c in contributors)
    if total_commits == 0:
        return 0

    target = total_commits * threshold
    cumulative = 0
    count = 0

    for c in contributors:
        if _contributor_is_bot(c):
            continue
        cumulative += c.commits
        count += 1
        if cumulative >= target:
            return count

    return count


# pylint: disable=too-many-locals
def _classify_contributors(
    contributors: list[Contributor],
    commits: list,
    now: datetime,
) -> tuple[ContributorDetail | None, ContributorDetail | None, int]:
    """Classify contributors into archetypes.

    Returns:
        (lead, historical_lead, minor_count)
    """
    # Filter out bots
    humans = [c for c in contributors if not _contributor_is_bot(c)]

    if not humans:
        return None, None, 0

    # Compute recent activity (last 12 months)
    twelve_months_ago = now - timedelta(days=365)
    recent_commits_by_author: dict[str, int] = {}

    for commit in commits:
        if commit.author_date and commit.author_date >= twelve_months_ago:
            # Use GitHub login only (not email, which may be protected/masked)
            author = commit.author_login
            if not author or author == "unknown":
                continue  # Skip commits without a GitHub login
            if not _is_bot(author):
                recent_commits_by_author[author] = (
                    recent_commits_by_author.get(author, 0) + 1
                )

    # Find lead (dominant over last 12 months)
    lead = None
    if recent_commits_by_author:
        total_recent = sum(recent_commits_by_author.values())
        if total_recent > 0:
            # Find author with most recent commits
            top_author = max(recent_commits_by_author, key=recent_commits_by_author.get)  # type: ignore[arg-type]
            top_count = recent_commits_by_author[top_author]
            # Lead if >30% of recent commits
            if top_count / total_recent > 0.3:
                lead = ContributorDetail(
                    login=top_author,
                    commits=top_count,
                    archetype=ContributorArchetype.LEAD,
                )

    # Find historical lead (dominant overall but not active recently)
    historical_lead = None
    if humans:
        top_overall = humans[0]  # Already sorted by commits
        if top_overall.login not in recent_commits_by_author:
            # Not active in last 12 months
            historical_lead = ContributorDetail(
                login=top_overall.login,
                commits=top_overall.commits,
                archetype=ContributorArchetype.HISTORICAL_LEAD,
            )
        elif lead and top_overall.login != lead.login:
            # Different from current lead and less active recently
            recent_count = recent_commits_by_author.get(top_overall.login, 0)
            if recent_count < (lead.commits * 0.3):
                historical_lead = ContributorDetail(
                    login=top_overall.login,
                    commits=top_overall.commits,
                    archetype=ContributorArchetype.HISTORICAL_LEAD,
                )

    # Count minor contributors (<=2 commits)
    minor_count = sum(1 for c in humans if c.commits <= 2)

    return lead, historical_lead, minor_count


def _compute_activity_trend(commits: list, now: datetime) -> dict[str, int]:
    """Compute commit counts for different time windows."""
    windows = {
        "3m": 90,
        "6m": 180,
        "12m": 365,
        "24m": 730,
    }

    trend = {}
    for label, days in windows.items():
        cutoff = now - timedelta(days=days)
        count = sum(
            1 for c in commits
            if c.author_date and c.author_date >= cutoff
        )
        trend[label] = count

    return trend


def _compute_bot_ratio(contributors: list[Contributor]) -> float:
    """Compute ratio of bot commits to total commits."""
    if not contributors:
        return 0.0

    total_commits = sum(c.commits for c in contributors)
    if total_commits == 0:
        return 0.0

    bot_commits = sum(c.commits for c in contributors if _contributor_is_bot(c))
    return bot_commits / total_commits


def analyze_contributors(repo: Repository) -> ContributorsIndicator:
    """Analyze contributor patterns from repository data.

    Returns a ContributorsIndicator with:
    - Total authors (excluding bots)
    - Bus factor
    - Bot ratio
    - Lead and historical lead
    - Minor contributor count
    - Activity trend
    - Status and interpretation
    """
    now = datetime.now(timezone.utc)
    contributors = repo.contributors.contributors

    # Filter out bots for author count
    humans = [c for c in contributors if not _contributor_is_bot(c)]
    total_authors = len(humans)

    # Bus factor
    bus_factor = _compute_bus_factor(contributors)

    # Bot ratio
    bot_ratio = _compute_bot_ratio(contributors)

    # Classify contributors
    lead, historical_lead, minor_count = _classify_contributors(
        contributors, repo.commits, now
    )

    # Activity trend
    activity_trend = _compute_activity_trend(repo.commits, now)

    indicator = ContributorsIndicator(
        total_authors=total_authors,
        bus_factor=bus_factor,
        bot_ratio=bot_ratio,
        lead=lead,
        historical_lead=historical_lead,
        minor_count=minor_count,
        activity_trend=activity_trend,
    )

    indicator.status = _compute_status(indicator)
    indicator.interpretation = _build_interpretation(indicator)

    return indicator


def _compute_status(ind: ContributorsIndicator) -> Status:
    """Compute contributor health status."""
    # No contributors at all
    if ind.total_authors == 0:
        return Status.CRITICAL

    # Very low bus factor
    if ind.bus_factor == 1:
        return Status.CRITICAL

    # Low bus factor
    if ind.bus_factor == 2:
        return Status.WARNING

    # No recent activity
    if ind.activity_trend.get("3m", 0) == 0:
        return Status.WARNING

    return Status.HEALTHY


def _build_interpretation(ind: ContributorsIndicator) -> str:
    """Build human-readable interpretation."""
    parts = []

    parts.append(f"{ind.total_authors} authors")
    parts.append(f"bus factor: {ind.bus_factor}")

    if ind.bot_ratio > 0:
        parts.append(f"bots: {ind.bot_ratio:.0%}")

    if ind.lead:
        parts.append(f"lead: {ind.lead.login}")

    if ind.historical_lead:
        parts.append(f"historical: {ind.historical_lead.login}")

    if ind.minor_count > 0:
        parts.append(f"{ind.minor_count} minor")

    # Activity summary
    commits_3m = ind.activity_trend.get("3m", 0)
    commits_12m = ind.activity_trend.get("12m", 0)
    if commits_3m > 0:
        parts.append(f"{commits_3m} commits (3m)")
    elif commits_12m > 0:
        parts.append(f"{commits_12m} commits (12m)")
    else:
        parts.append("no recent activity")

    return ", ".join(parts)
