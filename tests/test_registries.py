"""Tests for package registry detection and fetching."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gh_score.core.cache import Cache
from gh_score.core.fetchers.registries import (
    _compare_licenses,
    _detect_ecosystems,
    _extract_crate_name,
    _extract_docker_image_name,
    _extract_gem_name,
    _extract_go_module_path,
    _extract_maven_coordinates,
    _extract_npm_package_name,
    _extract_package_name,
    _extract_python_package_name,
    _fetch_crates,
    _fetch_docker,
    _fetch_go,
    _fetch_go_imported_by,
    _fetch_maven,
    _fetch_npm_downloads,
    _fetch_pypi,
    _fetch_pypi_downloads,
    _fetch_rubygems,
    _parse_docker_response,
    _parse_maven_response,
    _parse_npm_response,
    _parse_pypi_response,
    _parse_rubygems_response,
    fetch_registry_info,
)
from gh_score.core.models import LicenseInfo, RegistryInfo, RepoUrl, Repository


def _mock_http_response(status_code: int, payload: dict) -> MagicMock:
    """Build a fake httpx response with the given status and JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _mock_async_client(*responses: MagicMock) -> AsyncMock:
    """Build a mocked httpx.AsyncClient context manager whose .get returns
    the given responses in order."""
    instance = AsyncMock()
    if len(responses) == 1:
        instance.get = AsyncMock(return_value=responses[0])
    else:
        instance.get = AsyncMock(side_effect=list(responses))
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    return instance


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


# ---------------------------------------------------------------------------
# Remaining package name extractors
# ---------------------------------------------------------------------------

class TestMoreExtractors:
    def test_crate_from_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "mycrate"')
        assert _extract_crate_name(tmp_path) == "mycrate"

    def test_go_module_path(self, tmp_path):
        (tmp_path / "go.mod").write_text("module github.com/acme/widgets\n")
        assert _extract_go_module_path(tmp_path) == "github.com/acme/widgets"

    def test_gem_from_gemspec(self, tmp_path):
        (tmp_path / "mygem.gemspec").write_text('spec.name = "mygem"')
        assert _extract_gem_name(tmp_path) == "mygem"

    def test_maven_coordinates(self, tmp_path):
        (tmp_path / "pom.xml").write_text(
            "<project><groupId>com.acme</groupId>"
            "<artifactId>widgets</artifactId></project>"
        )
        assert _extract_maven_coordinates(tmp_path) == "com.acme:widgets"

    def test_docker_image_from_compose(self, tmp_path):
        (tmp_path / "docker-compose.yml").write_text(
            "services:\n  web:\n    image: nginx:latest\n"
        )
        assert _extract_docker_image_name(tmp_path) == "nginx:latest"

    def test_python_from_setup_cfg(self, tmp_path):
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = mypkg\n")
        assert _extract_python_package_name(tmp_path) == "mypkg"

    def test_dispatcher_routes(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "x"}')
        assert _extract_package_name(tmp_path, "npm") == "x"

    def test_dispatcher_unknown_ecosystem(self, tmp_path):
        assert _extract_package_name(tmp_path, "unknown-eco") is None


# ---------------------------------------------------------------------------
# Response parsers (pure, edge cases)
# ---------------------------------------------------------------------------

class TestParsers:
    def test_pypi_yanked_is_deprecated(self):
        data = {
            "info": {"version": "1.0.0"},
            "releases": {"1.0.0": [{"yanked": True}]},
        }
        info = _parse_pypi_response(data, RegistryInfo(ecosystem="pypi", package_name="x"))
        assert info.deprecated is True

    def test_pypi_invalid_date_ignored(self):
        data = {
            "info": {"version": "1.0.0"},
            "releases": {"1.0.0": [{"upload_time_iso_8601": "garbage"}]},
        }
        info = _parse_pypi_response(data, RegistryInfo(ecosystem="pypi", package_name="x"))
        assert info.latest_date is None

    def test_npm_deprecated_string(self):
        data = {
            "dist-tags": {"latest": "1.0.0"},
            "time": {},
            "versions": {"1.0.0": {"deprecated": "use v2"}},
        }
        info = _parse_npm_response(data, RegistryInfo(ecosystem="npm", package_name="x"))
        assert info.deprecated is True

    def test_npm_not_deprecated(self):
        data = {
            "dist-tags": {"latest": "1.0.0"},
            "time": {},
            "versions": {"1.0.0": {}},
        }
        info = _parse_npm_response(data, RegistryInfo(ecosystem="npm", package_name="x"))
        assert info.deprecated is False

    def test_rubygems_license_list_joined(self):
        data = {"name": "gem", "version": "1.0", "license": ["MIT", "BSD-2-Clause"]}
        info = _parse_rubygems_response(
            data, RegistryInfo(ecosystem="rubygems", package_name="x")
        )
        assert info.registry_license == "MIT, BSD-2-Clause"

    def test_maven_empty_docs_not_found(self):
        info = _parse_maven_response(
            {"response": {"docs": []}}, RegistryInfo(ecosystem="maven", package_name="x")
        )
        assert info.exists is False

    def test_docker_private_is_deprecated(self):
        data = {"name": "secret", "pull_count": 0, "is_private": True}
        info = _parse_docker_response(
            data, RegistryInfo(ecosystem="docker", package_name="x")
        )
        assert info.deprecated is True


# ---------------------------------------------------------------------------
# Fetchers (HTTP mocked away)
# ---------------------------------------------------------------------------

class TestFetchers:
    @pytest.mark.asyncio
    async def test_pypi_success(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "info": {"version": "1.2.3", "license": "MIT"},
            "releases": {"1.2.3": [{"upload_time_iso_8601": "2023-01-15T10:00:00Z"}]},
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            info = await _fetch_pypi("mypkg", cache)

        assert info.exists is True
        assert info.latest_version == "1.2.3"
        assert info.registry_license == "MIT"
        assert info.latest_date == datetime(2023, 1, 15, 10, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_pypi_not_found(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(404, {})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            info = await _fetch_pypi("does-not-exist", cache)
        assert info.exists is False

    @pytest.mark.asyncio
    async def test_pypi_cache_hit_skips_network(self, tmp_path):
        cache = Cache(str(tmp_path))
        cache.set_json("pypi:mypkg", {"info": {"version": "9.9.9"}}, ttl_seconds=3600)
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            info = await _fetch_pypi("mypkg", cache)
        mock_cls.assert_not_called()
        assert info.latest_version == "9.9.9"

    @pytest.mark.asyncio
    async def test_pypi_downloads(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"data": {"last_month": 12345}})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            count = await _fetch_pypi_downloads("mypkg", cache)
        assert count == 12345

    @pytest.mark.asyncio
    async def test_npm_downloads_scoped_url_encoded(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"downloads": 42})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(resp)
            mock_cls.return_value = instance
            count = await _fetch_npm_downloads("@scope/pkg", cache)

        assert count == 42
        url = instance.get.await_args.args[0]
        assert "scope%2Fpkg" in url

    @pytest.mark.asyncio
    async def test_crates_success(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "crate": {
                "newest_version": "2.0.0",
                "downloads": 1000,
                "recent_downloads": 50,
                "license": "MIT",
                "updated_at": "2023-01-01T00:00:00Z",
            },
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            info = await _fetch_crates("mycrate", cache)

        assert info.exists is True
        assert info.latest_version == "2.0.0"
        assert info.downloads == 1000
        assert info.recent_downloads == 50
        assert info.registry_license == "MIT"

    @pytest.mark.asyncio
    async def test_go_success(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "module": {
                "latestVersion": "v1.0.0",
                "license": "BSD-3-Clause",
                "updatedAt": "2023-01-01T00:00:00Z",
            },
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            info = await _fetch_go("example.com/mod", cache)

        assert info.exists is True
        assert info.latest_version == "v1.0.0"
        assert info.registry_license == "BSD-3-Clause"

    @pytest.mark.asyncio
    async def test_go_imported_by_count(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"importedBy": [{"path": "a"}, {"path": "b"}]})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            count = await _fetch_go_imported_by("example.com/mod", cache)
        assert count == 2

    @pytest.mark.asyncio
    async def test_rubygems_success(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "name": "rails",
            "version": "7.0.0",
            "downloads": 5000,
            "version_downloads": 100,
            "license": "MIT",
            "updated_at": "2023-01-01T00:00:00Z",
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            info = await _fetch_rubygems("rails", cache)

        assert info.exists is True
        assert info.latest_version == "7.0.0"
        assert info.downloads == 5000
        assert info.recent_downloads == 100

    @pytest.mark.asyncio
    async def test_maven_success(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "response": {
                "docs": [{
                    "latestVersion": "1.0.0",
                    "downloadCount": 999,
                    "timestamp": 1672531200000,  # 2023-01-01T00:00:00Z
                }],
            },
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            info = await _fetch_maven("com.acme:widgets", cache)

        assert info.exists is True
        assert info.latest_version == "1.0.0"
        assert info.downloads == 999
        # fromtimestamp is naive/local; compare epoch seconds to stay tz-independent
        assert info.latest_date is not None
        assert info.latest_date.timestamp() == 1672531200.0

    @pytest.mark.asyncio
    async def test_maven_without_colon_skips_network(self, tmp_path):
        cache = Cache(str(tmp_path))
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(_mock_http_response(200, {}))
            mock_cls.return_value = instance
            info = await _fetch_maven("no-colon-here", cache)

        instance.get.assert_not_called()
        assert info.exists is False

    @pytest.mark.asyncio
    async def test_docker_official_image_uses_library(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "name": "nginx",
            "pull_count": 1000,
            "last_updated": "2023-01-01T00:00:00Z",
            "is_private": False,
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(resp)
            mock_cls.return_value = instance
            info = await _fetch_docker("nginx", cache)

        assert info.exists is True
        assert info.downloads == 1000
        url = instance.get.await_args.args[0]
        assert "/library/nginx" in url


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

class TestOrchestration:
    @pytest.mark.asyncio
    async def test_multiple_ecosystems(self, tmp_path):
        """A repo with package.json AND pyproject.toml queries both registries."""
        (tmp_path / "package.json").write_text(json.dumps({"name": "web-lib"}))
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "pylib"')
        repo = _make_repo()
        cache = Cache(str(tmp_path))

        # Ecosystems are detected in pattern order: pypi first, then npm.
        pypi_resp = _mock_http_response(200, {"info": {"version": "2.0.0"}, "releases": {}})
        pypi_dl = _mock_http_response(200, {"data": {"last_month": 20}})
        npm_resp = _mock_http_response(200, {
            "name": "web-lib",
            "dist-tags": {"latest": "1.0.0"},
            "time": {},
            "versions": {},
        })
        npm_dl = _mock_http_response(200, {"downloads": 10})

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(pypi_resp, pypi_dl, npm_resp, npm_dl)
            result = await fetch_registry_info(repo, local_path=str(tmp_path), cache=cache)

        assert {r.ecosystem for r in result} == {"pypi", "npm"}
        by_eco = {r.ecosystem: r for r in result}
        assert by_eco["pypi"].latest_version == "2.0.0"
        assert by_eco["pypi"].downloads == 20
        assert by_eco["npm"].latest_version == "1.0.0"
        assert by_eco["npm"].recent_downloads == 10


class TestCompareLicenses:
    def _make_registry(self, license_: str | None) -> RegistryInfo:
        return RegistryInfo(
            ecosystem="pypi", package_name="x", exists=True, registry_license=license_
        )

    def test_match(self):
        reg = self._make_registry("MIT")
        repo = _make_repo()
        repo.license = LicenseInfo(spdx_id="MIT")
        _compare_licenses([reg], repo)
        assert reg.license_matches_github is True

    def test_mismatch(self):
        reg = self._make_registry("GPL-3.0")
        repo = _make_repo()
        repo.license = LicenseInfo(spdx_id="MIT")
        _compare_licenses([reg], repo)
        assert reg.license_matches_github is False

    def test_no_github_license_skips(self):
        reg = self._make_registry("MIT")
        repo = _make_repo()  # no license
        _compare_licenses([reg], repo)
        assert reg.license_matches_github is None
