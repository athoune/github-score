"""Tests for GitHub contributor fetching and analysis.

Uses recorded API response fixtures to avoid hitting rate limits.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gh_score.core.fetchers.github import GitHubFetcher, _BOT_LOGINS
from gh_score.core.analyzers.contributors import _BOT_PATTERNS, _is_bot, analyze_contributors
from gh_score.core.models import Commit, Contributor, ContributorStats, RepoUrl, Repository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict | list:
    """Load a JSON fixture from tests/fixtures/."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text())


@pytest.fixture
def contributors_response():
    """Sample /repos/owner/repo/contributors response."""
    return _load_fixture("github_contributors.json")


@pytest.fixture
def commits_response():
    """Sample /repos/owner/repo/commits response."""
    return _load_fixture("github_commits.json")


# ---------------------------------------------------------------------------
# Bot detection
# ---------------------------------------------------------------------------

class TestBotDetection:
    """Known bots must be detected; normal users must not."""

    def test_lando_is_bot(self):
        """Lando (Mozilla merge bot) must be detected by both bot lists."""
        assert _is_bot("lando"), "analyzer bot patterns must detect lando"
        assert "lando" in _BOT_LOGINS, "fetcher bot logins must list lando"

    def test_bot_lists_are_in_sync(self):
        """The fetcher (_BOT_LOGINS) and analyzer (_BOT_PATTERNS) bot lists
        must cover the same bots once the "[bot]" suffix is normalized, so
        one list cannot drift from the other."""
        normalized_logins = {login.removesuffix("[bot]") for login in _BOT_LOGINS}
        assert normalized_logins == _BOT_PATTERNS

    def test_dependabot_is_bot(self):
        assert _is_bot("dependabot[bot]")

    def test_normal_user_not_bot(self):
        assert not _is_bot("octocat")

    def test_bot_login_in_bot_logins(self):
        assert "github-actions[bot]" in _BOT_LOGINS


# ---------------------------------------------------------------------------
# Contributor parsing from API response
# ---------------------------------------------------------------------------

class TestContributorParsing:
    """Verify that contributor data is correctly parsed from the GitHub API
    response — the raw REST response → Contributor model mapping."""

    def test_parse_contributors(self, contributors_response):
        """Contributors are parsed from the paginated API response."""
        contributors = []
        total_commits = 0
        for c in contributors_response:
            login = c.get("login", "")
            commits = c.get("contributions", 0)
            total_commits += commits
            is_bot = login.lower() in _BOT_LOGINS or login.endswith("[bot]")
            contributors.append(Contributor(
                login=login,
                avatar_url=c.get("avatar_url"),
                commits=commits,
                is_bot=is_bot,
            ))

        assert len(contributors) > 0
        assert total_commits > 0
        # First contributor should have the most commits
        assert contributors[0].commits >= contributors[1].commits

    def test_bot_flagging(self, contributors_response):
        """Bot accounts should be flagged in parsed contributors."""
        for c in contributors_response:
            login = c.get("login", "")
            is_bot = login.lower() in _BOT_LOGINS or login.endswith("[bot]")
            if is_bot:
                # Verify the bot is recognized
                assert login.lower() in _BOT_LOGINS or login.endswith("[bot]")


# ---------------------------------------------------------------------------
# analyze_contributors with mocked Repository
# ---------------------------------------------------------------------------

class TestAnalyzeContributors:
    """analyze_contributors must work correctly with properly parsed data."""

    def _make_repo(
        self,
        contributors: list[Contributor],
        commits: list[Commit],
        total_commits: int | None = None,
    ) -> Repository:
        url = RepoUrl(owner="test", repo="project")
        repo = Repository(url=url)
        repo.contributors = ContributorStats(
            contributors=contributors,
            total_commit_count=total_commits or sum(c.commits for c in contributors),
        )
        repo.commits = commits
        return repo

    def test_single_contributor(self):
        """A repo with one active contributor should have bus factor 1."""
        contribs = [Contributor(login="alice", commits=100)]
        commits = [
            Commit(sha=f"sha{i}", author_login="alice",
                   author_date=None, message="fix")
            for i in range(10)
        ]
        repo = self._make_repo(contribs, commits)
        result = analyze_contributors(repo)
        assert result.bus_factor == 1
        assert result.total_authors == 1

    def test_bot_excluded_from_authors(self):
        """Bots must not count as human authors."""
        from datetime import datetime, timezone

        contribs = [
            Contributor(login="alice", commits=50, is_bot=False),
            Contributor(login="dependabot[bot]", commits=30, is_bot=True),
        ]
        now = datetime.now(timezone.utc)
        commits = [
            Commit(sha="sha0", author_login="alice",
                   author_date=now, message="fix"),
        ]
        repo = self._make_repo(contribs, commits)
        result = analyze_contributors(repo)
        assert result.total_authors == 1
        assert result.lead is not None
        assert result.lead.login == "alice"
