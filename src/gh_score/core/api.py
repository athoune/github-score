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
    analyze_security,
    analyze_sustainability,
    analyze_website,
)
from gh_score.core.analyzers.fork import classify_fork
from gh_score.core.analyzers.mirror import detect_mirror
from gh_score.core.cache import Cache
from gh_score.core.comparison import (
    ComparisonResult,
    apply_subject_refinement,
    compare_results,
)
from gh_score.core.fetchers.github import GitHubFetcher
from gh_score.core.fetchers.local_git import fetch_local_repo
from gh_score.core.fetchers.registries import fetch_registry_info
from gh_score.core.fetchers.website import probe_website
from gh_score.core.models import AnalysisResult, RepoUrl
from gh_score.i18n import t
from gh_score.llm.provider import (
    analyze_qualitative_with_llm,
    analyze_recommendation_with_llm,
    assess_subjects_with_llm,
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

    fetcher: GitHubFetcher | None = None
    api_meta_ok = False
    try:
        if is_local:
            # Local analysis
            repo = fetch_local_repo(str(path))

            # Fetch additional data from GitHub API if we have a URL
            if repo.url:
                if not _token_available(config):
                    warnings.append(t("warn_no_token"))
                fetcher = GitHubFetcher(config, cache)
                # Enrich with API data
                api_repo = await fetcher.fetch_all(repo.url)
                # Merge: prefer local data for commits/contributors, API for
                # metadata. Only merge when the API answered: a failed fetch
                # would replace good local data with empty fields.
                api_meta_ok = bool(api_repo.meta.full_name)
                if api_meta_ok:
                    repo.meta = api_repo.meta
                    repo.license = api_repo.license
                    repo.release_health = api_repo.release_health
                    repo.languages = api_repo.languages
                    repo.community = api_repo.community
                    repo.issues = api_repo.issues
                    repo.security_updates = await fetcher.fetch_security_updates(repo.url)
                # Keep local commits and contributors (more complete)
            local_path = str(path)
        else:
            # Remote analysis
            repo_url = RepoUrl.parse(url_or_path)
            if not _token_available(config):
                warnings.append(t("warn_no_token"))
            fetcher = GitHubFetcher(config, cache)
            repo = await fetcher.fetch_all(repo_url)
            repo.security_updates = await fetcher.fetch_security_updates(repo_url)
            api_meta_ok = bool(repo.meta.full_name)
            local_path = None

        # Fetch registry information. Remote mode reuses the still-open
        # fetcher to read manifests through the GitHub contents API, so
        # registry detection works without a local clone.
        remote_reader = None
        if not is_local and fetcher is not None:
            live_fetcher = fetcher  # non-None reference for the closure

            async def _read_manifest(name: str) -> str | None:
                return await live_fetcher.fetch_file_content(repo.url, name)

            remote_reader = _read_manifest
        repo.registries = await fetch_registry_info(
            repo,
            local_path,
            cache,
            remote_reader=remote_reader,
            libraries_io_key=config.registries.libraries_io_api_key,
        )

        # Fork divergence + PR lookup: measure how far the fork's default
        # branch is from its parent and find the pull requests opened from
        # this fork, so a PR-vehicle fork (soft) is not judged as an
        # independent project. Requires the still-open fetcher.
        if fetcher is not None and repo.meta.fork and repo.meta.parent_full_name:
            repo.meta.fork_ahead, repo.meta.fork_behind = (
                await fetcher.fetch_fork_divergence(
                    repo.url,
                    repo.meta.parent_full_name,
                    repo.meta.default_branch,
                )
            )
            repo.meta.fork_prs = await fetcher.fetch_fork_prs(
                repo.meta.parent_full_name, repo.url.owner
            )
        repo.meta.is_soft_fork = classify_fork(repo.meta.fork_ahead)

        # Hardening: never analyze a ghost repository. When the GitHub API
        # could not return the repository metadata, surface it clearly
        # instead of silently analyzing empty data: 404 means the repository
        # does not exist (hard error); anything else (network, rate limit,
        # auth) degrades the analysis with a warning.
        if fetcher is not None and not api_meta_ok:
            status = await fetcher.probe_status(repo.url.api_url)
            if status == 404:
                raise ValueError(t("error_repo_not_found", url=str(repo.url)))
            warnings.append(t("warn_api_unreachable", url=str(repo.url)))
    finally:
        if fetcher is not None:
            await fetcher.close()

    # Mirror-only repositories: flag them so the report points at the
    # upstream instead of judging a repo where no development happens.
    repo.meta.is_mirror, repo.meta.mirror_upstream = detect_mirror(
        repo.meta.mirror_url, repo.meta.description, repo.readme_content
    )

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
        security=analyze_security(repo),
        root_files=repo.community.root_files,
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


async def compare_repos_async(
    urls_or_paths: list[str],
    config: Config | None = None,
    use_local: bool = False,
) -> ComparisonResult:
    """Analyze several repositories and assess their comparability.

    Args:
        urls_or_paths: GitHub URLs or local paths
        config: Configuration (loads defaults if None)
        use_local: Force local analysis for every target

    Returns:
        ComparisonResult with per-pair comparability assessments
    """
    if config is None:
        config = Config.load()

    results = await asyncio.gather(
        *(analyze_repo_async(url, config, use_local) for url in urls_or_paths)
    )
    comparison = compare_results(list(results))
    await refine_comparison_subjects(comparison, config)
    return comparison


async def refine_comparison_subjects(
    comparison: ComparisonResult, config: Config
) -> None:
    """Lift deterministic ``unknown`` subject verdicts with the optional
    LLM (spec §8.4). Never overrides a deterministic verdict; appends any
    LLM warning to ``comparison.warnings``. No-op when the LLM is
    disabled."""
    if not config.llm.enabled:
        return
    llm_warnings: list[str] = []
    verdicts = await assess_subjects_with_llm(
        comparison.projects, config.llm, llm_warnings
    )
    apply_subject_refinement(comparison, verdicts)
    comparison.warnings = list(dict.fromkeys([*comparison.warnings, *llm_warnings]))


def compare_repos(
    urls_or_paths: list[str],
    config: Config | None = None,
    use_local: bool = False,
) -> ComparisonResult:
    """Analyze several repositories and assess their comparability (sync
    wrapper for :func:`compare_repos_async`)."""
    return asyncio.run(compare_repos_async(urls_or_paths, config, use_local))
