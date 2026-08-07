"""Tests for the analysis pipeline (api.py).

The pipeline is exercised end-to-end with the network mocked away:
GitHubFetcher, fetch_local_repo and fetch_registry_info are replaced, so
the tests verify the orchestration (path selection, data merging, LLM
opt-in, analyzer wiring) rather than the fetchers themselves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gh_score.config import Config
from gh_score.core.api import analyze_repo, analyze_repo_async
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
    LLMRecommendation,
    MaintenanceState,
    QualitativeSignals,
    RecommendationLevel,
    Release,
    ReleaseHealth,
    RepoUrl,
    Repository,
    RepositoryMeta,
    SecurityUpdate,
    WebsiteInfo,
)


def _make_repo_data() -> Repository:
    """Raw repository data as 'fetch_all' would return it."""
    now = datetime.now(timezone.utc)
    return Repository(
        url=RepoUrl("owner", "repo"),
        meta=RepositoryMeta(
            stars=1000,
            forks=100,
            created_at=now - timedelta(days=500),
            description="demo",
        ),
        license=LicenseInfo(
            spdx_id="MIT", family=LicenseFamily.PERMISSIVE, osi_approved=True
        ),
        release_health=ReleaseHealth(releases=[
            Release(tag_name="v1.0.0", published_at=now - timedelta(days=5)),
            Release(tag_name="v0.9.0", published_at=now - timedelta(days=40)),
        ]),
        contributors=ContributorStats(
            contributors=[
                Contributor(login="alice", commits=100),
                Contributor(login="bob", commits=50),
            ],
            total_commit_count=150,
        ),
        commits=[
            Commit(
                sha=f"c{i}",
                author_login="alice",
                author_date=now - timedelta(days=i),
            )
            for i in range(40)
        ],
        issues=[
            Issue(
                number=1,
                title="x",
                state="closed",
                created_at=now - timedelta(days=10),
                closed_at=now - timedelta(days=2),
            ),
        ],
        languages=LanguageBreakdown(languages={"Python": 8000, "HTML": 2000}),
        community=CommunityFiles(has_readme=True, funding={"github": ["alice"]}),
    )


def _make_config(tmp_path) -> Config:
    config = Config()
    config.github.token = "test-token"  # avoids the fetcher stderr warning
    config.cache.dir = str(tmp_path)
    config.llm.enabled = False
    return config


def _mock_fetcher(mock_cls, repo: Repository) -> MagicMock:
    """Configure the GitHubFetcher class mock to return `repo` from
    fetch_all, and record the close() call."""
    instance = MagicMock()
    instance.fetch_all = AsyncMock(return_value=repo)
    instance.fetch_security_updates = AsyncMock(return_value=[])
    instance.close = AsyncMock()
    mock_cls.return_value = instance
    return instance


class TestRemotePath:
    @pytest.mark.asyncio
    async def test_runs_pipeline(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ) as mock_registries,
        ):
            instance = _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        # Fetcher constructed with (config, cache) and closed
        mock_fetcher_cls.assert_called_once()
        fetcher_config, fetcher_cache = mock_fetcher_cls.call_args.args
        assert fetcher_config is config
        assert isinstance(fetcher_cache, Cache)
        instance.fetch_all.assert_awaited_once()
        instance.close.assert_awaited_once()

        # Registries fetched in remote mode with local_path=None
        mock_registries.assert_awaited_once()
        assert mock_registries.await_args.args[1] is None

        # All analyzers ran and produced a result
        assert result.url == RepoUrl("owner", "repo")
        assert result.meta.stars == 1000
        assert result.registries == []
        assert result.release_health.latest_version == "v1.0.0"
        assert result.contributors.total_authors == 2
        assert result.maintenance.state == MaintenanceState.ACTIVE
        assert result.languages.primary == "Python"
        assert result.recommendation.level == RecommendationLevel.GREEN
        assert result.recommendation.message != ""

    @pytest.mark.asyncio
    async def test_invalid_url_raises(self, tmp_path):
        config = _make_config(tmp_path)
        with pytest.raises(ValueError, match="Not a valid GitHub repository URL"):
            await analyze_repo_async("https://gitlab.com/owner/repo", config)

    @pytest.mark.asyncio
    async def test_config_defaults_to_load(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.Config.load", return_value=config) as mock_load,
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            await analyze_repo_async("https://github.com/owner/repo")

        mock_load.assert_called_once()


class TestLocalPath:
    @pytest.mark.asyncio
    async def test_enriches_from_api(self, tmp_path):
        config = _make_config(tmp_path)
        local_repo = _make_repo_data()
        api_repo = _make_repo_data()
        api_repo.meta.stars = 9999  # API data must win for metadata

        with (
            patch(
                "gh_score.core.api.fetch_local_repo", return_value=local_repo
            ) as mock_local,
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ) as mock_registries,
        ):
            instance = _mock_fetcher(mock_fetcher_cls, api_repo)
            result = await analyze_repo_async(str(tmp_path), config, use_local=True)

        mock_local.assert_called_once_with(str(tmp_path))
        # Enriched from the API...
        assert result.meta.stars == 9999
        # ...with the registry fetch pointing at the local path
        assert mock_registries.await_args.args[1] == str(tmp_path)
        # Local commits kept (40 commits → active maintenance)
        assert result.maintenance.state == MaintenanceState.ACTIVE
        # Fetcher closed even on the local path
        instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_detected_from_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        config = _make_config(tmp_path)
        repo = _make_repo_data()

        with (
            patch(
                "gh_score.core.api.fetch_local_repo", return_value=repo
            ) as mock_local,
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            await analyze_repo_async(str(tmp_path), config)

        mock_local.assert_called_once_with(str(tmp_path))

    @pytest.mark.asyncio
    async def test_without_remote_skips_api(self, tmp_path):
        config = _make_config(tmp_path)
        local_repo = _make_repo_data()
        local_repo.url = None  # type: ignore[assignment]  # no GitHub remote

        with (
            patch(
                "gh_score.core.api.fetch_local_repo", return_value=local_repo
            ) as mock_local,
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, _make_repo_data())
            await analyze_repo_async(str(tmp_path), config, use_local=True)

        mock_local.assert_called_once_with(str(tmp_path))
        mock_fetcher_cls.assert_not_called()


class TestLlmIntegration:
    @pytest.mark.asyncio
    async def test_signals_attached_when_enabled(self, tmp_path):
        config = _make_config(tmp_path)
        config.llm.enabled = True
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(return_value=QualitativeSignals(
                    roadmap="v2 planned",
                    text_maintenance_state="active",
                )),
            ) as mock_llm,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        mock_llm.assert_awaited_once()
        assert result.qualitative.available is True
        assert result.qualitative.roadmap == "v2 planned"
        assert result.qualitative.text_maintenance_state == "active"

    @pytest.mark.asyncio
    async def test_not_called_when_disabled(self, tmp_path):
        config = _make_config(tmp_path)  # llm.enabled = False
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(return_value=QualitativeSignals()),
            ) as mock_llm,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            await analyze_repo_async("https://github.com/owner/repo", config)

        mock_llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_qualitative_always_present(self, tmp_path):
        config = _make_config(tmp_path)  # llm disabled
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.qualitative.available is False

    @pytest.mark.asyncio
    async def test_refined_recommendation_attached_when_enabled(self, tmp_path):
        config = _make_config(tmp_path)
        config.llm.enabled = True
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(return_value=QualitativeSignals()),
            ),
            patch(
                "gh_score.core.api.analyze_recommendation_with_llm",
                new=AsyncMock(return_value=LLMRecommendation(
                    level="green",
                    message="Solid project",
                    explanation="Active and well documented.",
                    confidence=0.8,
                )),
            ) as mock_rec,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        mock_rec.assert_awaited_once()
        assert result.llm_recommendation is not None
        assert result.llm_recommendation.level == "green"
        # The deterministic verdict is still computed.
        assert result.recommendation.level == RecommendationLevel.GREEN

    @pytest.mark.asyncio
    async def test_refined_recommendation_none_when_disabled(self, tmp_path):
        config = _make_config(tmp_path)  # llm disabled
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.llm_recommendation is None


class TestWarnings:
    @staticmethod
    def _pin_fr(monkeypatch):
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)

    @pytest.mark.asyncio
    async def test_token_missing_warning(self, tmp_path, monkeypatch):
        self._pin_fr(monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        config = _make_config(tmp_path)
        config.github.token = ""
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert any("Token GitHub" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_token_warning_when_token_set(self, tmp_path, monkeypatch):
        self._pin_fr(monkeypatch)
        config = _make_config(tmp_path)  # token = "test-token"
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_remote_llm_without_key_warning(self, tmp_path, monkeypatch):
        self._pin_fr(monkeypatch)
        config = _make_config(tmp_path)
        config.llm.enabled = True
        config.llm.base_url = "https://api.openai.com/v1"
        config.llm.api_key = ""
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(return_value=QualitativeSignals()),
            ) as mock_qualitative,
            patch(
                "gh_score.core.api.analyze_recommendation_with_llm",
                new=AsyncMock(return_value=None),
            ) as mock_recommendation,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert any("clé API" in w for w in result.warnings)
        # The LLM calls must be skipped entirely: no key, remote provider.
        mock_qualitative.assert_not_awaited()
        mock_recommendation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_failure_warning_propagated(self, tmp_path, monkeypatch):
        self._pin_fr(monkeypatch)
        config = _make_config(tmp_path)
        config.llm.enabled = True
        config.llm.base_url = "http://localhost:11434/v1"  # local: usable without key
        repo = _make_repo_data()

        def _failing_llm(repo, cfg, warnings=None):
            if warnings is not None:
                warnings.append("LLM exploded")
            return QualitativeSignals()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(side_effect=_failing_llm),
            ),
            patch(
                "gh_score.core.api.analyze_recommendation_with_llm",
                new=AsyncMock(return_value=None),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert "LLM exploded" in result.warnings

    @pytest.mark.asyncio
    async def test_warnings_deduplicated(self, tmp_path, monkeypatch):
        self._pin_fr(monkeypatch)
        config = _make_config(tmp_path)
        config.llm.enabled = True
        config.llm.base_url = "http://localhost:11434/v1"
        repo = _make_repo_data()

        def _double_warning_llm(repo, cfg, warnings=None):
            if warnings is not None:
                warnings.append("same warning")
                warnings.append("same warning")
            return QualitativeSignals()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(side_effect=_double_warning_llm),
            ),
            patch(
                "gh_score.core.api.analyze_recommendation_with_llm",
                new=AsyncMock(return_value=None),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.warnings == ["same warning"]


class TestSyncWrapper:
    def test_analyze_repo_forwards_to_async(self, tmp_path):
        config = _make_config(tmp_path)
        with patch(
            "gh_score.core.api.analyze_repo_async",
            new=AsyncMock(return_value="RESULT"),
        ) as mock_async:
            result = analyze_repo("https://github.com/owner/repo", config, use_local=True)

        assert result == "RESULT"
        mock_async.assert_awaited_once()
        assert mock_async.await_args.args == ("https://github.com/owner/repo", config, True)


class TestWebsiteProbe:
    """The homepage probe runs only when a homepage is declared."""

    @pytest.mark.asyncio
    async def test_homepage_probed_and_stored(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()
        repo.meta.homepage = "https://example.com"
        probed = WebsiteInfo(url="https://example.com", status_code=200)

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.probe_website",
                new=AsyncMock(return_value=probed),
            ) as mock_probe,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        mock_probe.assert_awaited_once()
        assert mock_probe.await_args.args[0] == "https://example.com"
        assert result.website.url == "https://example.com"
        assert result.website.status_code == 200
        assert result.website.status.name == "HEALTHY"

    @pytest.mark.asyncio
    async def test_no_homepage_skips_probe(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()  # meta.homepage stays None

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch("gh_score.core.api.probe_website", new=AsyncMock()) as mock_probe,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        mock_probe.assert_not_awaited()
        assert result.website.status.name == "UNKNOWN"


class TestSecurityUpdates:
    """Open Dependabot security PRs flow through the pipeline."""

    @pytest.mark.asyncio
    async def test_fetched_and_analyzed(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()
        updates = [
            SecurityUpdate(number=1, title="Bump pkg", url="https://github.com/o/r/pull/1"),
        ]

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch("gh_score.core.api.probe_website", new=AsyncMock()),
        ):
            instance = _mock_fetcher(mock_fetcher_cls, repo)
            instance.fetch_security_updates = AsyncMock(return_value=updates)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        instance.fetch_security_updates.assert_awaited_once()
        assert result.security.pending_count == 1
        assert result.security.status.name == "WARNING"


class TestMirrorDetection:
    """The pipeline flags mirror-only repositories from meta + readme."""

    @pytest.mark.asyncio
    async def test_read_only_mirror_description(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()
        repo.meta.description = "A read-only mirror of the upstream project"

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch("gh_score.core.api.probe_website", new=AsyncMock()),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.meta.is_mirror is True

    @pytest.mark.asyncio
    async def test_plain_repo_not_mirror(self, tmp_path):
        config = _make_config(tmp_path)
        repo = _make_repo_data()  # description="demo"

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch("gh_score.core.api.probe_website", new=AsyncMock()),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.meta.is_mirror is False
