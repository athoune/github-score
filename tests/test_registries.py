"""Tests for package registry detection and fetching."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from gh_score.core.cache import Cache
from gh_score.core.fetchers.registries import (
    _detect_ecosystems,
    _extract_package_name,
    _extract_npm_package_name,
    _extract_python_package_name,
    fetch_registry_info,
)
from gh_score.core.models import RepoUrl, Repository, RegistryInfo


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------

class TestDetectEcosystems:
    """Tests for _detect_ecosystems."""

    def test_npm_project(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert _detect_ecosystems(tmp_path) == ["npm"]

    def test_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "foo"')
        assert _detect_ecosystems(tmp_path) == ["pypi"]

    def test_rust_project(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"')
        assert _detect_ecosystems(tmp_path) == ["crates.io"]

    def test_go_project(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/foo")
        assert _detect_ecosystems(tmp_path) == ["go"]

    def test_no_manifests(self, tmp_path):
        assert _detect_ecosystems(tmp_path) == []

    def test_none_path(self):
        assert _detect_ecosystems(None) == []

    def test_nonexistent_path(self):
        assert _detect_ecosystems(Path("/nonexistent")) == []


# ---------------------------------------------------------------------------
# Package name extraction from config files
# ---------------------------------------------------------------------------

class TestExtractPackageName:
    """Package names must come from config files, NOT from the repo name."""

    def test_npm_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "@scope/my-lib"}))
        assert _extract_npm_package_name(tmp_path) == "@scope/my-lib"

    def test_npm_missing(self, tmp_path):
        assert _extract_npm_package_name(tmp_path) is None

    def test_python_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-project"')
        assert _extract_python_package_name(tmp_path) == "my-project"

    def test_python_no_name(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0"')
        assert _extract_python_package_name(tmp_path) is None


# ---------------------------------------------------------------------------
# Registry info fetching — no inference from repo name
# ---------------------------------------------------------------------------

def _make_repo(owner: str = "acme", repo: str = "widgets") -> Repository:
    """Build a minimal Repository for tests."""
    return Repository(url=RepoUrl(owner=owner, repo=repo))


class TestFetchRegistryInfo:
    """fetch_registry_info must only use names from config files, never infer
    from the repository name.  When no manifest is found the ecosystem must
    be skipped entirely."""

    @pytest.mark.asyncio
    async def test_no_ecosystem_returns_empty(self, tmp_path):
        repo = _make_repo()
        cache = Cache(str(tmp_path))
        result = await fetch_registry_info(repo, local_path=None, cache=cache)
        assert result == []

    @pytest.mark.asyncio
    async def test_npm_with_package_json(self, tmp_path):
        """When package.json exists with a valid name, query the registry."""
        (tmp_path / "package.json").write_text(json.dumps({"name": "left-pad"}))
        repo = _make_repo(owner="jake", repo="left-pad")
        cache = Cache(str(tmp_path))

        # Mock the HTTP call to npm
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "name": "left-pad",
            "dist-tags": {"latest": "1.3.0"},
            "time": {"1.3.0": "2023-01-01T00:00:00.000Z"},
            "versions": {"1.3.0": {}},
        }

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await fetch_registry_info(
                repo, local_path=str(tmp_path), cache=cache
            )

        assert len(result) == 1
        assert result[0].ecosystem == "npm"
        assert result[0].package_name == "left-pad"
        assert result[0].is_heuristic is False

    @pytest.mark.asyncio
    async def test_no_heuristic_inference(self, tmp_path):
        """When package.json exists but has a DIFFERENT name from the repo,
        the package name from config must be used — NOT the repo name."""
        (tmp_path / "package.json").write_text(json.dumps({"name": "completely-different-name"}))
        repo = _make_repo(owner="acme", repo="widgets")
        cache = Cache(str(tmp_path))

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {}

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await fetch_registry_info(
                repo, local_path=str(tmp_path), cache=cache
            )

        assert len(result) == 1
        # Must use the name from package.json, NOT "widgets" (the repo name)
        assert result[0].package_name == "completely-different-name"
        assert result[0].is_heuristic is False

    @pytest.mark.asyncio
    async def test_missing_config_skips_ecosystem(self, tmp_path):
        """When a local path is provided but no manifest file exists for an
        ecosystem, the ecosystem must be skipped — no name inference from
        the repo name."""
        repo = _make_repo(owner="acme", repo="widgets")
        cache = Cache(str(tmp_path))

        result = await fetch_registry_info(
            repo, local_path=str(tmp_path), cache=cache
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_package_json_no_inference(self, tmp_path):
        """When package.json exists but has no "name" field, the ecosystem
        is detected but the package name cannot be extracted.  The code
        must NOT fall back to inferring the name from the repo name."""
        (tmp_path / "package.json").write_text("{}")
        repo = _make_repo(owner="acme", repo="widgets")
        cache = Cache(str(tmp_path))

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await fetch_registry_info(
                repo, local_path=str(tmp_path), cache=cache
            )

        # The ecosystem is detected (package.json exists) but we should NOT
        # infer "widgets" from the repo name.  Currently the code DOES infer
        # it — this test documents the bug.
        if result:
            # BUG: currently infers "widgets" from repo name
            assert result[0].package_name != "widgets", (
                "Package name must not be inferred from repo name"
            )
