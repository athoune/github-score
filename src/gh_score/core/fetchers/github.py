"""GitHub REST API fetcher.

Fetches all repository data needed for health analysis.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

from gh_score.config import Config
from gh_score.core.cache import Cache
from gh_score.core.models import (
    Commit,
    CommunityFiles,
    Contributor,
    ContributorStats,
    Issue,
    LanguageBreakdown,
    LicenseFamily,
    LicenseInfo,
    Release,
    ReleaseHealth,
    RepoUrl,
    Repository,
    RepositoryMeta,
)


# Known bot accounts
_BOT_LOGINS = frozenset({
    "dependabot[bot]", "dependabot", "renovate[bot]", "renovate",
    "github-actions[bot]", "greenkeeper[bot]", "snyk-bot",
    "codecov[bot]", "allcontributors[bot]", "imgbot[bot]",
    "stale[bot]", "mergify[bot]", "pre-commit-ci[bot]",
    "lando",
})


def _parse_datetime(s: str | None) -> datetime | None:
    """Parse ISO 8601 datetime string from GitHub API."""
    if not s:
        return None
    try:
        # GitHub returns "2024-01-15T10:30:00Z" format
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _classify_license(spdx_id: str | None) -> LicenseFamily:
    """Classify a license into a family based on SPDX ID."""
    if not spdx_id or spdx_id == "NOASSERTMENT":
        return LicenseFamily.OTHER

    spdx = spdx_id.upper()

    if spdx in ("UNLICENSE", "CC0-1.0", "WTFPL", "0BSD"):
        return LicenseFamily.PUBLIC_DOMAIN

    if spdx.startswith(("GPL", "AGPL", "LGPL", "EUPL", "CECILL")):
        return LicenseFamily.COPYLEFT

    if spdx.startswith(("MIT", "APACHE", "BSD", "ISC", "ZLIB", "PSF", "MPL")):
        return LicenseFamily.PERMISSIVE

    return LicenseFamily.OTHER


class GitHubFetcher:
    """Fetches repository data from the GitHub REST API."""

    BASE_URL = "https://api.github.com"

    def __init__(self, config: Config, cache: Cache):
        self.config = config
        self.cache = cache
        self.token = config.github.token or os.environ.get("GITHUB_TOKEN", "")

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "gh-score/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        else:
            print(
                "Warning: No GITHUB_TOKEN set. Using anonymous requests "
                "(60 requests/hour limit).",
                file=sys.stderr,
            )

        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, url: str, params: dict | None = None) -> dict | list | None:
        """Make a cached GET request to the GitHub API."""
        cache_key = f"github:{url}:{json.dumps(params or {}, sort_keys=True)}"
        cached = self.cache.get_json(cache_key)
        if cached is not None:
            return cached

        try:
            resp = await self.client.get(url, params=params)

            # Check rate limit
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining and int(remaining) < 10:
                print(
                    f"Warning: GitHub API rate limit low ({remaining} remaining).",
                    file=sys.stderr,
                )

            resp.raise_for_status()
            data = resp.json()

            # Cache successful response
            ttl = self.config.cache.ttl_hours * 3600
            self.cache.set_json(cache_key, data, ttl)

            return data
        except httpx.HTTPStatusError:
            return None
        except httpx.RequestError:
            return None

    async def _get_all_pages(
        self, url: str, params: dict | None = None, max_pages: int = 10
    ) -> list[dict]:
        """Fetch all pages of a paginated endpoint."""
        results: list[dict] = []
        params = {**(params or {}), "per_page": "100"}

        for page in range(1, max_pages + 1):
            params["page"] = str(page)
            data = await self._get(url, params)
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            results.extend(data)
            if len(data) < 100:
                break

        return results

    async def fetch_meta(self, url: RepoUrl) -> RepositoryMeta:
        """Fetch repository metadata."""
        data = await self._get(url.api_url)
        if not data or not isinstance(data, dict):
            return RepositoryMeta()

        owner = data.get("owner", {}) or {}
        raw_type = owner.get("type", "")
        owner_type = {"User": "user", "Organization": "organization"}.get(raw_type, "")

        return RepositoryMeta(
            name=data.get("name", ""),
            full_name=data.get("full_name", ""),
            owner=owner.get("login", ""),
            owner_type=owner_type,
            description=data.get("description"),
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
            pushed_at=_parse_datetime(data.get("pushed_at")),
            default_branch=data.get("default_branch", "main"),
            archived=data.get("archived", False),
            disabled=data.get("disabled", False),
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            watchers=data.get("subscribers_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            topics=data.get("topics", []),
            has_wiki=data.get("has_wiki", False),
            homepage=data.get("homepage"),
            size_kb=data.get("size", 0),
        )

    async def fetch_license(self, url: RepoUrl) -> LicenseInfo:
        """Fetch license information."""
        data = await self._get(f"{url.api_url}/license")
        if not data or not isinstance(data, dict):
            return LicenseInfo()

        license_data = data.get("license", {})
        spdx_id = license_data.get("spdx_id")
        osi = license_data.get("spdx_id") in (
            "MIT", "Apache-2.0", "GPL-2.0", "GPL-3.0", "LGPL-2.1",
            "LGPL-3.0", "MPL-2.0", "BSD-2-Clause", "BSD-3-Clause",
            "ISC", "Unlicense", "AGPL-3.0", "AGPL-2.0", "ECL-2.0",
        )

        return LicenseInfo(
            spdx_id=spdx_id if spdx_id != "NOASSERTMENT" else None,
            name=license_data.get("name"),
            osi_approved=osi,
            family=_classify_license(spdx_id),
        )

    async def fetch_releases(self, url: RepoUrl) -> ReleaseHealth:
        """Fetch release information."""
        data = await self._get_all_pages(f"{url.api_url}/releases", max_pages=5)
        releases = []
        for r in data:
            if not isinstance(r, dict):
                continue
            releases.append(Release(
                tag_name=r.get("tag_name", ""),
                name=r.get("name"),
                published_at=_parse_datetime(r.get("published_at")),
                prerelease=r.get("prerelease", False),
                draft=r.get("draft", False),
                html_url=r.get("html_url"),
            ))
        return ReleaseHealth(releases=releases)

    async def fetch_contributors(self, url: RepoUrl) -> ContributorStats:
        """Fetch contributor statistics."""
        data = await self._get_all_pages(
            f"{url.api_url}/contributors",
            params={"anon": "false"},
            max_pages=5,
        )

        contributors = []
        total_commits = 0
        for c in data:
            if not isinstance(c, dict):
                continue
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

        # Enrich contributors with email domains from commits
        commits = await self.fetch_commits(url, months=12)
        email_by_login: dict[str, str] = {}
        for commit in commits:
            if commit.author_login and commit.author_email:
                email_by_login[commit.author_login] = commit.author_email

        for contributor in contributors:
            email = email_by_login.get(contributor.login)
            if email and "@" in email:
                contributor.email_domain = email.split("@")[-1].lower()

        return ContributorStats(
            contributors=contributors,
            total_commit_count=total_commits,
        )

    async def fetch_commits(
        self, url: RepoUrl, months: int = 12
    ) -> list[Commit]:
        """Fetch commit history for the last N months."""
        since = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()
        data = await self._get_all_pages(
            f"{url.api_url}/commits",
            params={"since": since, "sha": "HEAD"},
            max_pages=10,
        )

        commits = []
        for c in data:
            if not isinstance(c, dict):
                continue
            commit_data = c.get("commit", {})
            author = c.get("author") or {}
            commits.append(Commit(
                sha=c.get("sha", ""),
                author_login=author.get("login"),
                author_email=commit_data.get("author", {}).get("email"),
                author_date=_parse_datetime(
                    commit_data.get("author", {}).get("date")
                ),
                message=commit_data.get("message", ""),
            ))

        return commits

    async def fetch_issues(self, url: RepoUrl, months: int = 12) -> list[Issue]:
        """Fetch issues (including PRs) for the last N months."""
        since = (datetime.now(timezone.utc) - timedelta(days=months * 30)).isoformat()
        data = await self._get_all_pages(
            f"{url.api_url}/issues",
            params={
                "since": since,
                "state": "all",
                "sort": "created",
                "direction": "desc",
            },
            max_pages=10,
        )

        issues = []
        for i in data:
            if not isinstance(i, dict):
                continue
            issues.append(Issue(
                number=i.get("number", 0),
                title=i.get("title", ""),
                state=i.get("state", "open"),
                created_at=_parse_datetime(i.get("created_at")) or datetime.now(timezone.utc),
                closed_at=_parse_datetime(i.get("closed_at")),
                is_pull_request="pull_request" in i,
                labels=[lb.get("name", "") for lb in i.get("labels", []) if isinstance(lb, dict)],
            ))

        return issues

    async def fetch_languages(self, url: RepoUrl) -> LanguageBreakdown:
        """Fetch language breakdown from GitHub Linguist."""
        data = await self._get(f"{url.api_url}/languages")
        if not data or not isinstance(data, dict):
            return LanguageBreakdown()

        languages = {k: v for k, v in data.items() if isinstance(v, int)}
        return LanguageBreakdown(languages=languages)

    async def fetch_community_files(self, url: RepoUrl) -> CommunityFiles:
        """Check for community health files."""
        files_to_check = {
            "readme.md": "has_readme",
            "contributing.md": "has_contributing",
            "code_of_conduct.md": "has_code_of_conduct",
            "security.md": "has_security",
            "governance.md": "has_governance",
            "changelog.md": "has_changelog",
            "news.md": "has_changelog",
            "license": "has_license",
            "license.md": "has_license",
        }

        community = CommunityFiles()

        # Check contents
        data = await self._get(f"{url.api_url}/contents")
        if data and isinstance(data, list):
            existing = {
                item.get("name", "").lower()
                for item in data
                if isinstance(item, dict)
            }
            for filename, attr in files_to_check.items():
                if filename in existing:
                    setattr(community, attr, True)

        # Fetch FUNDING.yml
        funding_data = await self._get(f"{url.api_url}/contents/.github/FUNDING.yml")
        if funding_data and isinstance(funding_data, dict):
            community.has_funding = True  # type: ignore[attr-defined]
            # Decode base64 content if available
            content = funding_data.get("content", "")
            encoding = funding_data.get("encoding", "")
            if content and encoding == "base64":
                try:
                    decoded = base64.b64decode(content).decode("utf-8")
                    community.funding = _parse_funding_yml(decoded)
                except Exception:
                    pass

        return community

    async def fetch_readme(self, url: RepoUrl) -> str | None:
        """Fetch README content."""
        data = await self._get(f"{url.api_url}/readme")
        if not data or not isinstance(data, dict):
            return None
        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if content and encoding == "base64":
            try:
                return base64.b64decode(content).decode("utf-8")
            except Exception:
                return None
        return None

    async def fetch_all(self, url: RepoUrl) -> Repository:
        """Fetch all data for a repository."""
        repo = Repository(url=url)

        # Fetch everything concurrently
        (
            repo.meta,
            repo.license,
            repo.release_health,
            repo.contributors,
            repo.languages,
            repo.community,
        ) = await asyncio.gather(
            self.fetch_meta(url),
            self.fetch_license(url),
            self.fetch_releases(url),
            self.fetch_contributors(url),
            self.fetch_languages(url),
            self.fetch_community_files(url),
        )

        repo.commits = await self.fetch_commits(url)
        repo.issues = await self.fetch_issues(url)
        repo.readme_content = await self.fetch_readme(url)

        return repo


def _parse_funding_yml(content: str) -> dict[str, list[str]]:
    """Parse FUNDING.yml content into a dict of platform -> accounts."""
    # Simple YAML-like parser for FUNDING.yml
    result: dict[str, list[str]] = {}
    current_key: str | None = None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line and not line.startswith((" ", "-", "\t")):
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()

            if value.startswith("[") and value.endswith("]"):
                # Inline list: github: [user1, user2]
                items = value[1:-1].split(",")
                result[key] = [i.strip().strip("'\"") for i in items if i.strip()]
                current_key = None
            elif value:
                result[key] = [value.strip("'\"")]
                current_key = None
            else:
                current_key = key
                result.setdefault(key, [])
        elif current_key and line.startswith("-"):
            # List item
            item = line[1:].strip().strip("'\"")
            if item:
                result.setdefault(current_key, []).append(item)

    return result
