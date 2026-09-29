"""Tests for custom-domain project URL resolution (back-link rule)."""

import pytest

from gh_score.core.models import RepoUrl
from gh_score.core.url_resolver import (
    extract_github_candidates,
    normalize_site_url,
    resolve_custom_domain,
)


class TestNormalizeSiteUrl:
    def test_scheme_and_www_ignored(self):
        assert normalize_site_url("http://www.example.com/a/") == normalize_site_url(
            "https://example.com/a"
        )

    def test_query_fragment_ignored(self):
        assert normalize_site_url("https://example.com/a?utm=1#top") == "example.com/a"

    def test_path_case_kept(self):
        assert normalize_site_url("https://example.com/Docs") != normalize_site_url(
            "https://example.com/docs"
        )


class TestIsForgeUrl:
    def test_gitlab_is_forge(self):
        from gh_score.core.url_resolver import is_forge_url

        assert is_forge_url("https://gitlab.com/owner/repo") is True
        assert is_forge_url("https://example.com/project") is False
        assert is_forge_url("https://owner.gitlab.io/project") is True


class TestExtractCandidates:
    def test_extract_unique(self):
        html = (
            '<a href="https://github.com/owner/repo">r</a>'
            '<a href="https://github.com/owner/repo/">dup</a>'
            '<a href="https://github.com/other/lib">l</a>'
        )
        got = extract_github_candidates(html)
        assert [(c.owner, c.repo) for c in got] == [
            ("owner", "repo"),
            ("other", "lib"),
        ]

    def test_extract_none(self):
        assert extract_github_candidates("<p>no links</p>") == []


async def _resolve(html, homepages, site="https://example.com/"):
    async def fetch_page(url):
        return (url, html)

    async def fetch_homepage(candidate: RepoUrl):
        return homepages.get(f"{candidate.owner}/{candidate.repo}")

    return await resolve_custom_domain(
        site, fetch_page=fetch_page, fetch_homepage=fetch_homepage
    )


class TestResolveCustomDomain:
    @pytest.mark.asyncio
    async def test_single_backlink_match(self):
        html = '<a href="https://github.com/owner/repo">repo</a>'
        got = await _resolve(html, {"owner/repo": "https://example.com/"})
        assert (got.owner, got.repo) == ("owner", "repo")

    @pytest.mark.asyncio
    async def test_no_candidate(self):
        with pytest.raises(ValueError, match="No GitHub repository link"):
            await _resolve("<p>hello</p>", {})

    @pytest.mark.asyncio
    async def test_no_backlink(self):
        html = '<a href="https://github.com/owner/repo">repo</a>'
        with pytest.raises(ValueError, match="claims .* as its homepage"):
            await _resolve(html, {"owner/repo": "https://other.com/"})

    @pytest.mark.asyncio
    async def test_missing_homepage(self):
        html = '<a href="https://github.com/owner/repo">repo</a>'
        with pytest.raises(ValueError, match="claims .* as its homepage"):
            await _resolve(html, {})

    @pytest.mark.asyncio
    async def test_ambiguous(self):
        html = (
            '<a href="https://github.com/a/one">1</a>'
            '<a href="https://github.com/b/two">2</a>'
        )
        with pytest.raises(ValueError, match="Ambiguous project page"):
            await _resolve(
                html,
                {"a/one": "https://example.com", "b/two": "https://example.com/"},
            )

    @pytest.mark.asyncio
    async def test_distractor_without_backlink_ignored(self):
        html = (
            '<a href="https://github.com/vendor/dep">dep</a>'
            '<a href="https://github.com/owner/repo">repo</a>'
        )
        got = await _resolve(
            html,
            {
                "vendor/dep": "https://vendor.com",
                "owner/repo": "http://www.example.com/",
            },
        )
        assert (got.owner, got.repo) == ("owner", "repo")

    @pytest.mark.asyncio
    async def test_fetch_failure(self):
        async def fetch_page(url):
            return None

        async def fetch_homepage(candidate: RepoUrl):
            return None

        with pytest.raises(ValueError, match="Could not fetch"):
            await resolve_custom_domain(
                "https://example.com/",
                fetch_page=fetch_page,
                fetch_homepage=fetch_homepage,
            )
