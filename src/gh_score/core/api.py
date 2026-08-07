"""Main API for analyzing GitHub repositories.

This is the primary entry point for the library.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from gh_score.config import Config
from gh_score.core.analyzers import (
    analyze_contributors,
    analyze_languages,
    analyze_license,
    analyze_maintenance,
    analyze_qualitative,
    analyze_recommendation,
    analyze_release_health,
    analyze_sustainability,
    analyze_website,
)
from gh_score.core.cache import Cache
from gh_score.core.fetchers.github import GitHubFetcher
from gh_score.core.fetchers.local_git import fetch_local_repo
from gh_score.core.fetchers.registries import fetch_registry_info
from gh_score.core.fetchers.website import probe_website
from gh_score.core.models import AnalysisResult, RepoUrl
from gh_score.i18n import t
from gh_score.llm.provider import (
    analyze_qualitative_with_llm,
    analyze_recommendation_with_llm,
)


def _token_available(config: Config) -> bool:
    """True when a GitHub token is configured (file, config or env)."""
    return bool(config.github.token or os.environ.get("GITHUB_TOKEN", ""))


def _is_local_llm(base_url: str) -> bool:
    """True when the LLM base URL points at a local server (no key needed)."""
    host = urlparse(base_url).hostname or ""
    return host in ("localhost", "127.0.0.1")


async def analyze_repo_async(
    url_or_path: str,
    config: Config | None = None,
    use_local: bool = False,
) -> AnalysisResult:
    """Analyze a GitHub repository (async version).

    Args:
        url_or_path: GitHub URL or local path to clone
        config: Configuration (loads defaults if None)
        use_local: Force local analysis even if URL provided

    Returns:
        AnalysisResult with all indicator families
    """
    if config is None:
        config = Config.load()

    cache = Cache(config.cache.dir, config.cache.ttl_hours * 3600)

    # Human-readable, localized warnings surfaced by the renderers
    # (TUI panel, or stderr for markdown/JSON).
    warnings: list[str] = []

    # Determine if we're analyzing locally or remotely
    path = Path(url_or_path)
    is_local = use_local or (path.exists() and (path / ".git").exists())

    if is_local:
        # Local analysis
        repo = fetch_local_repo(str(path))

        # Fetch additional data from GitHub API if we have a URL
        if repo.url:
            if not _token_available(config):
                warnings.append(t("warn_no_token"))
            fetcher = GitHubFetcher(config, cache)
            try:
                # Enrich with API data
                api_repo = await fetcher.fetch_all(repo.url)
                # Merge: prefer local data for commits/contributors, API for metadata
                repo.meta = api_repo.meta
                repo.license = api_repo.license
                repo.release_health = api_repo.release_health
                repo.languages = api_repo.languages
                repo.community = api_repo.community
                repo.issues = api_repo.issues
                # Keep local commits and contributors (more complete)
            finally:
                await fetcher.close()
    else:
        # Remote analysis
        repo_url = RepoUrl.parse(url_or_path)
        if not _token_available(config):
            warnings.append(t("warn_no_token"))
        fetcher = GitHubFetcher(config, cache)
        try:
            repo = await fetcher.fetch_all(repo_url)
        finally:
            await fetcher.close()

    # Fetch registry information
    local_path = str(path) if is_local else None
    repo.registries = await fetch_registry_info(repo, local_path, cache)

    # Probe the project homepage (skip when none is declared)
    if repo.meta.homepage:
        repo.website_info = await probe_website(repo.meta.homepage, cache)

    # Optional LLM analysis: qualitative signals (phase 1) + refined
    # recommendation (phase 2). Skipped entirely when the provider is
    # remote and no API key is configured.
    llm_enabled = config.llm.enabled
    if llm_enabled and not config.llm.api_key and not _is_local_llm(config.llm.base_url):
        warnings.append(t("warn_llm_no_api_key"))
        llm_enabled = False
    if llm_enabled:
        repo.llm_signals = await analyze_qualitative_with_llm(
            repo, config.llm, warnings
        )

    # Run all analyzers
    result = AnalysisResult(
        url=repo.url,
        meta=repo.meta,
        release_health=analyze_release_health(repo),
        license=analyze_license(repo),
        contributors=analyze_contributors(repo),
        maintenance=analyze_maintenance(repo),
        languages=analyze_languages(repo),
        sustainability=analyze_sustainability(repo),
        qualitative=analyze_qualitative(repo),
        registries=repo.registries,
        website=analyze_website(repo.website_info),
    )

    # Cross-cutting recommendation (needs the full result)
    result.recommendation = analyze_recommendation(result)

    # Optional LLM refined recommendation (phase 2): complementary, never
    # replaces the deterministic verdict above.
    if llm_enabled:
        result.llm_recommendation = await analyze_recommendation_with_llm(
            result, config.llm, warnings
        )

    # De-duplicate: the qualitative and recommendation LLM calls both fail
    # together and would append the same warning twice.
    warnings = list(dict.fromkeys(warnings))
    result.warnings = warnings
    return result


def analyze_repo(
    url_or_path: str,
    config: Config | None = None,
    use_local: bool = False,
) -> AnalysisResult:
    """Analyze a GitHub repository (sync wrapper).

    Args:
        url_or_path: GitHub URL or local path to clone
        config: Configuration (loads defaults if None)
        use_local: Force local analysis even if URL provided

    Returns:
        AnalysisResult with all indicator families
    """
    return asyncio.run(analyze_repo_async(url_or_path, config, use_local))
