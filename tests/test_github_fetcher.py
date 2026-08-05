"""Tests for the GitHub API fetcher (no network: HTTP is mocked)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from gh_score.config import Config
from gh_score.core.cache import Cache
from gh_score.core.fetchers.github import (
    GitHubFetcher,
    _classify_license,
    _parse_datetime,
    _parse_funding_yml,
)
from gh_score.core.models import LicenseFamily, RepoUrl


def _make_fetcher(tmp_path) -> GitHubFetcher:
    """Build a fetcher with a fake token (avoids the stderr warning) and an
    isolated cache dir."""
    config = Config()
    config.github.token = "test-token"
    config.cache.ttl_hours = 24
    return GitHubFetcher(config, Cache(str(tmp_path)))


URL = RepoUrl(owner="owner", repo="repo")


class TestParseDatetime:
    def test_zulu_format(self):
        parsed = _parse_datetime("2024-01-15T10:30:00Z")
        assert parsed == datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)

    def test_none(self):
        assert _parse_datetime(None) is None

    def test_empty(self):
        assert _parse_datetime("") is None

    def test_invalid(self):
        assert _parse_datetime("not-a-date") is None


class TestClassifyLicense:
    def test_none(self):
        assert _classify_license(None) == LicenseFamily.OTHER

    def test_noassertment(self):
        assert _classify_license("NOASSERTMENT") == LicenseFamily.OTHER

    def test_mit(self):
        assert _classify_license("MIT") == LicenseFamily.PERMISSIVE

    def test_apache(self):
        assert _classify_license("Apache-2.0") == LicenseFamily.PERMISSIVE

    def test_gpl(self):
        assert _classify_license("GPL-3.0") == LicenseFamily.COPYLEFT

    def test_agpl(self):
        assert _classify_license("AGPL-3.0") == LicenseFamily.COPYLEFT

    def test_cc0(self):
        assert _classify_license("CC0-1.0") == LicenseFamily.PUBLIC_DOMAIN

    def test_unlicense(self):
        assert _classify_license("UNLICENSE") == LicenseFamily.PUBLIC_DOMAIN

    def test_unknown(self):
        assert _classify_license("Custom-License") == LicenseFamily.OTHER


class TestGetAllPages:
    async def _fetcher(self, tmp_path):
        return _make_fetcher(tmp_path)

    @pytest.mark.asyncio
    async def test_full_then_partial_page(self, tmp_path):
        fetcher = await self._fetcher(tmp_path)
        page1 = [{"login": f"user{i}"} for i in range(100)]
        page2 = [{"login": "user100"}]

        # The production loop mutates its params dict in place, so recorded
        # mock args would alias the final state; capture a copy per call.
        seen_pages: list[dict] = []
        pages = [page1, page2]

        def fake_get(url, params):
            seen_pages.append(dict(params))
            return pages[len(seen_pages) - 1]

        fetcher._get = AsyncMock(side_effect=fake_get)

        result = await fetcher._get_all_pages(
            f"{URL.api_url}/contributors", params={"anon": "false"}
        )

        assert len(result) == 101
        # per_page injected, page incremented, stops after a short page
        assert seen_pages == [
            {"anon": "false", "per_page": "100", "page": "1"},
            {"anon": "false", "per_page": "100", "page": "2"},
        ]

    @pytest.mark.asyncio
    async def test_empty_first_page_stops(self, tmp_path):
        fetcher = await self._fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value=[])

        result = await fetcher._get_all_pages(f"{URL.api_url}/releases")

        assert result == []
        fetcher._get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_response_stops(self, tmp_path):
        fetcher = await self._fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value=None)

        result = await fetcher._get_all_pages(f"{URL.api_url}/releases")

        assert result == []

    @pytest.mark.asyncio
    async def test_max_pages_respected(self, tmp_path):
        fetcher = await self._fetcher(tmp_path)
        full_page = [{"login": "u"} for _ in range(100)]
        fetcher._get = AsyncMock(return_value=full_page)

        result = await fetcher._get_all_pages(
            f"{URL.api_url}/releases", max_pages=3
        )

        assert len(result) == 300
        assert len(fetcher._get.await_args_list) == 3


class TestFetchMeta:
    @pytest.mark.asyncio
    async def test_parses_metadata(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={
            "name": "repo",
            "full_name": "owner/repo",
            "owner": {"login": "owner"},
            "description": "A repo",
            "created_at": "2024-01-15T10:30:00Z",
            "default_branch": "main",
            "archived": True,
            "stargazers_count": 1234,
            "forks_count": 56,
            "subscribers_count": 7,
            "open_issues_count": 9,
            "topics": ["python", "cli"],
            "has_wiki": False,
            "homepage": "https://example.com",
            "size": 42,
        })

        meta = await fetcher.fetch_meta(URL)

        assert meta.full_name == "owner/repo"
        assert meta.stars == 1234
        assert meta.forks == 56
        assert meta.archived is True
        assert meta.topics == ["python", "cli"]
        assert meta.created_at == datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_missing_data_returns_empty(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value=None)

        meta = await fetcher.fetch_meta(URL)

        assert meta.name == ""
        assert meta.stars == 0


class TestFetchLicense:
    @pytest.mark.asyncio
    async def test_mit_license(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={
            "license": {"spdx_id": "MIT", "name": "MIT License"},
        })

        lic = await fetcher.fetch_license(URL)

        assert lic.spdx_id == "MIT"
        assert lic.osi_approved is True
        assert lic.family == LicenseFamily.PERMISSIVE

    @pytest.mark.asyncio
    async def test_noassertment_becomes_none(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={
            "license": {"spdx_id": "NOASSERTMENT", "name": "No license"},
        })

        lic = await fetcher.fetch_license(URL)

        assert lic.spdx_id is None
        assert lic.osi_approved is False
        assert lic.family == LicenseFamily.OTHER

    @pytest.mark.asyncio
    async def test_missing_license(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={})

        lic = await fetcher.fetch_license(URL)

        assert lic.spdx_id is None


class TestFetchReleases:
    @pytest.mark.asyncio
    async def test_parses_releases(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get_all_pages = AsyncMock(return_value=[
            {
                "tag_name": "v2.0.0",
                "name": "Release 2",
                "published_at": "2025-01-01T00:00:00Z",
                "prerelease": False,
                "draft": False,
                "html_url": "https://github.com/owner/repo/releases/tag/v2.0.0",
            },
            {
                "tag_name": "v3.0.0-beta",
                "published_at": "2025-06-01T00:00:00Z",
                "prerelease": True,
                "draft": True,
            },
        ])

        rh = await fetcher.fetch_releases(URL)

        assert len(rh.releases) == 2
        assert rh.latest is not None
        assert rh.latest.tag_name == "v2.0.0"  # drafts excluded
        assert rh.releases[1].prerelease is True
        assert rh.releases[1].draft is True


class TestFetchIssues:
    @pytest.mark.asyncio
    async def test_parses_issues_and_prs(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get_all_pages = AsyncMock(return_value=[
            {
                "number": 42,
                "title": "Bug",
                "state": "closed",
                "created_at": "2025-01-01T00:00:00Z",
                "closed_at": "2025-01-05T00:00:00Z",
                "labels": [{"name": "bug"}, {"name": "priority"}],
            },
            {
                "number": 43,
                "title": "PR",
                "state": "open",
                "created_at": "2025-02-01T00:00:00Z",
                "closed_at": None,
                "pull_request": {"url": "https://github.com/owner/repo/pull/43"},
                "labels": [],
            },
        ])

        issues = await fetcher.fetch_issues(URL)

        assert len(issues) == 2
        assert issues[0].labels == ["bug", "priority"]
        assert issues[0].closed_at is not None
        assert issues[0].is_pull_request is False
        assert issues[1].is_pull_request is True


class TestFetchLanguages:
    @pytest.mark.asyncio
    async def test_parses_languages(self, tmp_path):
        fetcher = _make_fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={
            "Python": 1000,
            "HTML": 200,
            "NonInt": "ignored",
        })

        langs = await fetcher.fetch_languages(URL)

        assert langs.languages == {"Python": 1000, "HTML": 200}
        assert langs.primary == "Python"


class TestParseFundingYml:
    def test_inline_list(self):
        result = _parse_funding_yml("github: [user1, user2]\n")
        assert result == {"github": ["user1", "user2"]}

    def test_single_value(self):
        result = _parse_funding_yml("patreon: mypage\n")
        assert result == {"patreon": ["mypage"]}

    def test_list_items(self):
        content = "open_collective:\n  - project-a\n  - project-b\n"
        result = _parse_funding_yml(content)
        assert result == {"open_collective": ["project-a", "project-b"]}

    def test_comments_and_blank_lines_ignored(self):
        result = _parse_funding_yml("# comment\n\ngithub: alice\n")
        assert result == {"github": ["alice"]}
