"""Maintenance state analyzer.

Analyzes maintenance patterns: last commit, issue velocity, staleness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gh_score.core.models import (
    MaintenanceIndicator,
    MaintenanceState,
    Repository,
    Status,
)


def _compute_last_commit_days(repo: Repository, now: datetime) -> int | None:
    """Compute days since last commit."""
    if not repo.commits:
        return None

    # Find most recent commit
    latest = max(
        (c for c in repo.commits if c.author_date),
        key=lambda c: c.author_date,  # type: ignore[arg-type]
        default=None,
    )

    if latest and latest.author_date:
        return (now - latest.author_date).days

    return None


def _compute_last_closed_days(repo: Repository, now: datetime) -> int | None:
    """Compute days since last closed issue/PR."""
    closed = [i for i in repo.issues if i.state == "closed" and i.closed_at]
    if not closed:
        return None

    latest = max(closed, key=lambda i: i.closed_at)  # type: ignore[arg-type]
    if latest.closed_at:
        return (now - latest.closed_at).days

    return None


def _compute_commits_per_month(repo: Repository, now: datetime) -> float | None:
    """Compute average commits per month over last 12 months."""
    twelve_months_ago = now - timedelta(days=365)
    recent = [
        c for c in repo.commits
        if c.author_date and c.author_date >= twelve_months_ago
    ]

    if not recent:
        return 0.0

    return len(recent) / 12.0


def _compute_issue_velocity(repo: Repository) -> float | None:
    """Compute median time to close issues (in days) for issues created in last 12 months."""
    now = datetime.now(timezone.utc)
    twelve_months_ago = now - timedelta(days=365)

    # Filter issues created in last 12 months and closed
    closed_issues = [
        i for i in repo.issues
        if i.created_at >= twelve_months_ago
        and i.state == "closed"
        and i.closed_at
        and not i.is_pull_request
    ]

    if not closed_issues:
        return None

    # Compute time to close for each
    close_times = []
    for issue in closed_issues:
        if issue.closed_at:
            days = (issue.closed_at - issue.created_at).total_seconds() / 86400
            close_times.append(days)

    if not close_times:
        return None

    # Median
    close_times.sort()
    n = len(close_times)
    if n % 2 == 0:
        return (close_times[n // 2 - 1] + close_times[n // 2]) / 2
    else:
        return close_times[n // 2]


def _compute_stale_issue_ratio(repo: Repository, now: datetime) -> float | None:
    """Compute ratio of open issues older than 12 months."""
    open_issues = [i for i in repo.issues if i.state == "open" and not i.is_pull_request]
    if not open_issues:
        return None

    twelve_months_ago = now - timedelta(days=365)
    stale = [i for i in open_issues if i.created_at < twelve_months_ago]

    return len(stale) / len(open_issues)


def _classify_state(
    last_commit_days: int | None,
    commits_per_month: float | None,
    last_closed_days: int | None,
) -> MaintenanceState:
    """Classify maintenance state."""
    # No commits at all
    if last_commit_days is None:
        return MaintenanceState.UNKNOWN

    # Abandoned: no commit for 6+ months
    if last_commit_days > 180:
        return MaintenanceState.ABANDONED

    # Active: commits within last month + consistent activity
    if last_commit_days <= 30 and commits_per_month and commits_per_month >= 2:
        return MaintenanceState.ACTIVE

    # Maintenance: infrequent commits but issues still closed
    if last_commit_days <= 180:
        if last_closed_days is not None and last_closed_days <= 90:
            return MaintenanceState.MAINTENANCE
        if commits_per_month and commits_per_month < 2:
            return MaintenanceState.MAINTENANCE

    return MaintenanceState.UNKNOWN


def analyze_maintenance(repo: Repository) -> MaintenanceIndicator:
    """Analyze maintenance state from repository data.

    Returns a MaintenanceIndicator with:
    - Last commit date and age
    - Last closed issue date
    - Commits per month
    - Issue velocity (median time to close)
    - Stale issue ratio
    - Maintenance state classification
    - Status and interpretation
    """
    now = datetime.now(timezone.utc)

    last_commit_days = _compute_last_commit_days(repo, now)
    last_closed_days = _compute_last_closed_days(repo, now)
    commits_per_month = _compute_commits_per_month(repo, now)
    issue_velocity = _compute_issue_velocity(repo)
    stale_ratio = _compute_stale_issue_ratio(repo, now)

    # Find last commit date
    last_commit_date = None
    if repo.commits:
        latest = max(
            (c for c in repo.commits if c.author_date),
            key=lambda c: c.author_date,  # type: ignore[arg-type]
            default=None,
        )
        if latest:
            last_commit_date = latest.author_date

    # Find last closed date
    last_closed_date = None
    closed = [i for i in repo.issues if i.state == "closed" and i.closed_at]
    if closed:
        latest_closed = max(closed, key=lambda i: i.closed_at)  # type: ignore[return-value]
        last_closed_date = latest_closed.closed_at

    # Classify state
    state = _classify_state(last_commit_days, commits_per_month, last_closed_days)

    indicator = MaintenanceIndicator(
        last_commit_date=last_commit_date,
        last_commit_days_ago=last_commit_days,
        last_closed_date=last_closed_date,
        commits_per_month=commits_per_month,
        issue_velocity_days=issue_velocity,
        stale_issue_ratio=stale_ratio,
        state=state,
    )

    indicator.status = _compute_status(indicator)
    indicator.interpretation = _build_interpretation(indicator)

    return indicator


def _compute_status(ind: MaintenanceIndicator) -> Status:
    """Compute maintenance status."""
    if ind.state == MaintenanceState.ABANDONED:
        return Status.CRITICAL

    if ind.state == MaintenanceState.ACTIVE:
        return Status.HEALTHY

    if ind.state == MaintenanceState.MAINTENANCE:
        return Status.WARNING

    # Unknown state
    if ind.last_commit_days_ago is None:
        return Status.UNKNOWN

    if ind.last_commit_days_ago > 180:
        return Status.CRITICAL

    if ind.last_commit_days_ago > 90:
        return Status.WARNING

    return Status.HEALTHY


def _build_interpretation(ind: MaintenanceIndicator) -> str:
    """Build human-readable interpretation."""
    parts = []

    # State
    state_desc = {
        MaintenanceState.ACTIVE: "active",
        MaintenanceState.MAINTENANCE: "maintenance mode",
        MaintenanceState.ABANDONED: "abandoned",
        MaintenanceState.UNKNOWN: "unknown",
    }
    parts.append(f"state: {state_desc.get(ind.state, 'unknown')}")

    # Last commit
    if ind.last_commit_days_ago is not None:
        if ind.last_commit_days_ago == 0:
            parts.append("last commit: today")
        elif ind.last_commit_days_ago == 1:
            parts.append("last commit: yesterday")
        else:
            parts.append(f"last commit: {ind.last_commit_days_ago}d ago")

    # Commit frequency
    if ind.commits_per_month is not None:
        if ind.commits_per_month >= 10:
            parts.append(f"{ind.commits_per_month:.1f} commits/month (very active)")
        elif ind.commits_per_month >= 2:
            parts.append(f"{ind.commits_per_month:.1f} commits/month (active)")
        elif ind.commits_per_month > 0:
            parts.append(f"{ind.commits_per_month:.1f} commits/month (low)")
        else:
            parts.append("no recent commits")

    # Issue velocity
    if ind.issue_velocity_days is not None:
        if ind.issue_velocity_days < 1:
            parts.append("issues closed: <1d")
        elif ind.issue_velocity_days < 7:
            parts.append(f"issues closed: {ind.issue_velocity_days:.0f}d")
        elif ind.issue_velocity_days < 30:
            parts.append(f"issues closed: {ind.issue_velocity_days:.0f}d (moderate)")
        else:
            parts.append(f"issues closed: {ind.issue_velocity_days:.0f}d (slow)")

    # Stale issues
    if ind.stale_issue_ratio is not None:
        if ind.stale_issue_ratio > 0.5:
            parts.append(f"{ind.stale_issue_ratio:.0%} stale issues")
        elif ind.stale_issue_ratio > 0.2:
            parts.append(f"{ind.stale_issue_ratio:.0%} stale issues")

    return ", ".join(parts)
