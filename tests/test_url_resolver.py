"""Tests for custom-domain project URL resolution (back-link rule)."""

import pytest

from gh_score.core.models import RepoUrl
from gh_score.core.url_resolver import (
    _MAX_SHORT_LINKS,
    extract_github_candidates,
    extract_short_link_urls,
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


class TestExtractShortLinks:
    def test_extract_unique(self):
        html = (
            '<a href="https://git.new/flipt">stars</a>'
            '<a href="https://git.new/flipt">dup</a>'
            '<a href="https://www.git.new/other">x</a>'
        )
        assert extract_short_link_urls(html) == [
            "https://git.new/flipt",
            "https://www.git.new/other",
        ]

    def test_extract_none(self):
        assert extract_short_link_urls("<p>no links</p>") == []
        # Generic shorteners are never extracted.
        assert extract_short_link_urls('<a href="https://bit.ly/abc">x</a>') == []

    def test_extract_capped(self):
        html = "".join(f'<a href="https://git.new/s{i}">x</a>' for i in range(30))
        assert len(extract_short_link_urls(html)) == _MAX_SHORT_LINKS


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


async def _resolve_with_shorts(html, homepages, shorts, site="https://example.com/"):
    """Resolve with stubbed git.new links: shorts maps URL -> RepoUrl|None."""

    async def fetch_page(url):
        return (url, html)

    async def fetch_homepage(candidate: RepoUrl):
        return homepages.get(f"{candidate.owner}/{candidate.repo}")

    async def follow_short_link(url):
        return shorts.get(url)

    return await resolve_custom_domain(
        site,
        fetch_page=fetch_page,
        fetch_homepage=fetch_homepage,
        follow_short_link=follow_short_link,
    )


class TestResolveShortLinks:
    @pytest.mark.asyncio
    async def test_short_link_only_page_resolves(self):
        """The flipt.io case: no direct github.com link, only git.new."""
        html = '<a href="https://git.new/flipt">Star on GitHub</a>'
        got = await _resolve_with_shorts(
            html,
            {"flipt-io/flipt": "https://flipt.io"},
            {"https://git.new/flipt": RepoUrl("flipt-io", "flipt")},
            site="https://www.flipt.io/",
        )
        assert (got.owner, got.repo) == ("flipt-io", "flipt")

    @pytest.mark.asyncio
    async def test_short_link_without_backlink_refused(self):
        html = '<a href="https://git.new/flipt">Star on GitHub</a>'
        with pytest.raises(ValueError, match="claims .* as its homepage"):
            await _resolve_with_shorts(
                html,
                {"flipt-io/flipt": "https://other.com"},
                {"https://git.new/flipt": RepoUrl("flipt-io", "flipt")},
            )

    @pytest.mark.asyncio
    async def test_dead_short_link_ignored(self):
        """A short link resolving to nothing leaves zero candidates."""
        html = '<a href="https://git.new/gone">x</a>'
        with pytest.raises(ValueError, match="No GitHub repository link"):
            await _resolve_with_shorts(html, {}, {"https://git.new/gone": None})

    @pytest.mark.asyncio
    async def test_short_link_dedupes_direct_link(self):
        html = (
            '<a href="https://github.com/owner/repo">repo</a>'
            '<a href="https://git.new/owner">x</a>'
        )
        calls = []

        async def fetch_page(url):
            return (url, html)

        async def fetch_homepage(candidate: RepoUrl):
            calls.append(f"{candidate.owner}/{candidate.repo}")
            return "https://example.com/"

        async def follow_short_link(url):
            return RepoUrl("owner", "repo")

        got = await resolve_custom_domain(
            "https://example.com/",
            fetch_page=fetch_page,
            fetch_homepage=fetch_homepage,
            follow_short_link=follow_short_link,
        )
        assert (got.owner, got.repo) == ("owner", "repo")
        # One back-link check: the duplicate was not added twice.
        assert calls == ["owner/repo"]
