"""Tests for GitHub contributor fetching and analysis.

Uses recorded API response fixtures to avoid hitting rate limits.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gh_score.config import Config
from gh_score.core.cache import Cache
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
# fetch_contributors with mocked API calls
# ---------------------------------------------------------------------------

class TestFetchContributors:
    """fetch_contributors must parse the real REST response — bot flagging,
    totals and email-domain enrichment included."""

    def _make_fetcher(self, tmp_path) -> GitHubFetcher:
        config = Config()
        config.github.token = "test-token"  # avoids the stderr warning
        config.cache.ttl_hours = 24
        return GitHubFetcher(config, Cache(str(tmp_path)))

    @pytest.mark.asyncio
    async def test_parses_fixture(self, tmp_path, contributors_response, commits_response):
        fetcher = self._make_fetcher(tmp_path)
        fetcher._get_all_pages = AsyncMock(return_value=contributors_response)

        commits = [
            Commit(
                sha=c.get("sha", ""),
                author_login=(c.get("author") or {}).get("login"),
                author_email=c.get("commit", {}).get("author", {}).get("email"),
            )
            for c in commits_response
        ]
        fetcher.fetch_commits = AsyncMock(return_value=commits)

        stats = await fetcher.fetch_contributors(RepoUrl(owner="owner", repo="repo"))

        assert stats.total_commit_count == 460  # 250+120+45+30+15
        assert len(stats.contributors) == 5
        assert stats.contributors[0].login == "alice"  # API order preserved

        by_login = {c.login: c for c in stats.contributors}
        assert by_login["alice"].is_bot is False
        assert by_login["dependabot[bot]"].is_bot is True
        assert by_login["lando"].is_bot is True  # listed in _BOT_LOGINS
        # Email enrichment: alice's email is resolved from the commits
        assert by_login["alice"].email_domain == "example.com"

    @pytest.mark.asyncio
    async def test_empty_response(self, tmp_path):
        fetcher = self._make_fetcher(tmp_path)
        fetcher._get_all_pages = AsyncMock(return_value=[])
        fetcher.fetch_commits = AsyncMock(return_value=[])

        stats = await fetcher.fetch_contributors(RepoUrl(owner="owner", repo="repo"))

        assert stats.contributors == []
        assert stats.total_commit_count == 0


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
