"""Main API for analyzing GitHub repositories.

This is the primary entry point for the library.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from gh_score.config import Config
from gh_score.core.analyzers import (
    analyze_contributors,
    analyze_languages,
    analyze_license,
    analyze_maintenance,
    analyze_release_health,
    analyze_sustainability,
)
from gh_score.core.cache import Cache
from gh_score.core.fetchers.github import GitHubFetcher
from gh_score.core.fetchers.local_git import fetch_local_repo
from gh_score.core.fetchers.registries import fetch_registry_info
from gh_score.core.models import AnalysisResult, RepoUrl, Repository
from gh_score.llm.provider import analyze_sustainability_with_llm


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

    # Determine if we're analyzing locally or remotely
    path = Path(url_or_path)
    is_local = use_local or (path.exists() and (path / ".git").exists())

    if is_local:
        # Local analysis
        repo = fetch_local_repo(str(path))

        # Fetch additional data from GitHub API if we have a URL
        if repo.url:
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
        fetcher = GitHubFetcher(config, cache)
        try:
            repo = await fetcher.fetch_all(repo_url)
        finally:
            await fetcher.close()

    # Fetch registry information
    local_path = str(path) if is_local else None
    repo.registries = await fetch_registry_info(repo, local_path, cache)

    # Optional LLM analysis for sustainability
    if config.llm.enabled:
        llm_signals = await analyze_sustainability_with_llm(repo, config.llm)
        # LLM signals could enrich the sustainability indicator
        # For now, we just run the rule-based analyzer

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
        registries=repo.registries,
    )

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
