"""Resolve a project website URL to its GitHub repository.

Two cases:

1. ``*.github.io`` URLs resolve deterministically via
   :meth:`RepoUrl.from_github_io` — no network needed.
2. Custom domains resolve only on a bidirectional link: the page must
   link to ``github.com/<owner>/<repo>`` candidates AND exactly one
   candidate must declare the page as its GitHub ``homepage`` (back-link).
   Anything else (no candidate, no back-link, several matches) raises a
   ``ValueError`` telling the user to pass the ``github.com`` URL directly.
   Guessing is worse than refusing: a wrong repo means a wrong verdict.

GitHub links hidden behind the ``git.new`` shortener (Dub's GitHub link
shortener, common on devtool marketing pages) are followed to their
redirect target — but only that allowlisted host, and only targets
landing on ``github.com/<owner>/<repo>`` become candidates. Following a
short link notifies the shortener's analytics; generic shorteners
(bit.ly, …) have no GitHub-specific semantics and are never followed.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import httpx

from gh_score.core.models import RepoUrl

# Matches github.com owner/repo links inside page HTML. Owner/repo charset
# follows GitHub naming (alphanumeric, dots, dashes, underscores).
_GITHUB_LINK_RE = re.compile(
    r"https?://(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
)

_USER_AGENT = "gh-score/0.1.0"
_PAGE_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0)
_MAX_REDIRECTS = 10
_MAX_BODY_BYTES = 512 * 1024

# Short-link hosts with documented GitHub-only semantics whose redirects we
# follow to recover hidden candidates. Generic shorteners are excluded: only
# hosts whose whole purpose is github.com redirection qualify.
_SHORTENER_HOSTS = ("git.new", "www.git.new")
# Upper bound on short links followed per page: each is one HTTP request.
_MAX_SHORT_LINKS = 10

# Matches git.new short links inside page HTML (slug is one or more path
# segments, e.g. https://git.new/flipt).
_GITNEW_LINK_RE = re.compile(
    r"https?://(?:www\.)?git\.new/(?P<slug>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
)

# Hosts that are code forges (or their Pages hosting), never custom project
# domains: a gitlab.com/owner/repo URL is unambiguously "not GitHub", not a
# project site to resolve via back-link.
_FORGE_HOSTS = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "sr.ht",
    "gitea.com",
    "sourceforge.net",
    "dev.azure.com",
)
_FORGE_SUFFIXES = (
    ".gitlab.io",
    ".bitbucket.io",
    ".codeberg.page",
)


def is_forge_url(url: str) -> bool:
    """True when the URL points at a known code forge, not a project site."""
    host = (urlparse(url.strip()).hostname or "").lower()
    if host in _FORGE_HOSTS:
        return True
    return host.endswith(_FORGE_SUFFIXES)


def normalize_site_url(url: str) -> str:
    """Normalize a site URL for homepage comparison.

    The scheme (http/https), a leading ``www.``, default ports, trailing
    slashes, queries and fragments are ignored — ``http://www.ex.com/a/``
    equals ``https://ex.com/a``. The path keeps its case.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    if port and not (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/") or ""
    return f"{host}{path}"


def extract_github_candidates(html: str) -> list[RepoUrl]:
    """Extract unique github.com owner/repo candidates from page HTML."""
    candidates: list[RepoUrl] = []
    seen: set[tuple[str, str]] = set()
    for m in _GITHUB_LINK_RE.finditer(html):
        repo = m.group("repo").removesuffix(".git").rstrip(").,;:'\"!")
        owner = m.group("owner").rstrip(").,;:'\"!")
        if not owner or not repo:
            continue
        key = (owner.lower(), repo.lower())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(RepoUrl(owner=owner, repo=repo))
    return candidates


def extract_short_link_urls(html: str) -> list[str]:
    """Extract unique git.new short-link URLs from page HTML (capped)."""
    urls: list[str] = []
    seen: set[str] = set()
    for m in _GITNEW_LINK_RE.finditer(html):
        url = m.group(0).rstrip(").,;:'\"!")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= _MAX_SHORT_LINKS:
            break
    return urls


def _github_repo_from_url(url: str) -> RepoUrl | None:
    """Parse a final URL into owner/repo when it is a github.com repo page."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if not owner or not repo:
        return None
    return RepoUrl(owner=owner, repo=repo)


FollowShortLink = Callable[[str], Awaitable[RepoUrl | None]]


async def follow_gitnew_link(url: str) -> RepoUrl | None:
    """Follow a git.new short link to its GitHub repository, if any.

    Only redirect targets landing on ``github.com/<owner>/<repo>`` are
    kept; anything else (parked slug, dead link, non-GitHub target,
    unreachable shortener) yields ``None``. The response body is never
    downloaded — only the final URL after redirects is used.
    """
    try:
        async with httpx.AsyncClient(
            timeout=_PAGE_TIMEOUT,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return None
                return _github_repo_from_url(str(resp.url))
    except httpx.RequestError:
        return None


FetchPage = Callable[[str], Awaitable[tuple[str, str] | None]]
FetchHomepage = Callable[[RepoUrl], Awaitable[str | None]]


async def fetch_page_http(url: str) -> tuple[str, str] | None:
    """GET a project page, following redirects. Returns (final_url, html)."""
    try:
        async with httpx.AsyncClient(
            timeout=_PAGE_TIMEOUT,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            body = resp.text[:_MAX_BODY_BYTES]
            return str(resp.url), body
    except httpx.RequestError:
        return None


async def resolve_custom_domain(
    site_url: str,
    *,
    fetch_page: FetchPage = fetch_page_http,
    fetch_homepage: FetchHomepage,
    follow_short_link: FollowShortLink = follow_gitnew_link,
) -> RepoUrl:
    """Resolve a custom-domain project page to its GitHub repository.

    Candidates come from direct ``github.com`` links plus ``git.new``
    short links followed to their redirect target. Requires exactly one
    candidate whose GitHub ``homepage`` points back at the page. Raises
    ``ValueError`` otherwise.
    """
    page = await fetch_page(site_url)
    if page is None:
        raise ValueError(
            f"Could not fetch project page {site_url!r} — pass the "
            "https://github.com/owner/repo URL directly"
        )
    final_url, html = page
    candidates = extract_github_candidates(html)
    seen = {(c.owner.lower(), c.repo.lower()) for c in candidates}
    for short_url in extract_short_link_urls(html):
        resolved = await follow_short_link(short_url)
        if resolved is None:
            continue
        key = (resolved.owner.lower(), resolved.repo.lower())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(resolved)
    if not candidates:
        raise ValueError(
            f"No GitHub repository link found on {site_url!r} — pass the "
            "https://github.com/owner/repo URL directly"
        )
    wanted = {normalize_site_url(site_url), normalize_site_url(final_url)}
    matches: list[RepoUrl] = []
    for candidate in candidates:
        homepage = await fetch_homepage(candidate)
        if homepage and normalize_site_url(homepage) in wanted:
            matches.append(candidate)
    if not matches:
        raise ValueError(
            f"No linked GitHub repository claims {site_url!r} as its homepage "
            "— pass the https://github.com/owner/repo URL directly"
        )
    if len(matches) > 1:
        names = ", ".join(f"{m.owner}/{m.repo}" for m in matches)
        raise ValueError(
            f"Ambiguous project page {site_url!r}: {len(matches)} linked "
            f"repositories claim it ({names}) — pass the "
            "https://github.com/owner/repo URL directly"
        )
    return matches[0]
