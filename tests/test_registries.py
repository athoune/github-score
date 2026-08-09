"""Tests for package registry detection and fetching."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gh_score.core.cache import Cache
from gh_score.core.fetchers.registries import (
    LocalManifestStore,
    RemoteManifestStore,
    _collect_ecosystems,
    _compare_licenses,
    _detect_ecosystems,
    _extract_crate_name,
    _extract_docker_image_name,
    _extract_gem_name,
    _extract_go_module_path,
    _extract_maven_coordinates,
    _extract_npm_package_name,
    _extract_package_name,
    _extract_pyproject_name,
    _extract_setup_cfg_name,
    _fetch_crates,
    _fetch_crates_dependents,
    _fetch_docker,
    _fetch_go,
    _fetch_go_imported_by,
    _fetch_libraries_io_dependents,
    _fetch_maven,
    _fetch_npm_downloads,
    _fetch_pypi,
    _fetch_pypi_downloads,
    _fetch_rubygems,
    _fetch_rubygems_dependents,
    _parse_docker_response,
    _parse_go_response,
    _parse_maven_response,
    _parse_npm_response,
    _parse_pypi_response,
    _parse_rubygems_response,
    fetch_registry_info,
)
from gh_score.core.models import LicenseInfo, RegistryInfo, RepoUrl, Repository


def _mock_http_response(status_code: int, payload: Any) -> MagicMock:
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


def _make_repo(owner: str = "acme", repo: str = "widgets") -> Repository:
    """Build a minimal Repository for tests."""
    return Repository(url=RepoUrl(owner=owner, repo=repo))


# ---------------------------------------------------------------------------
# Manifest stores
# ---------------------------------------------------------------------------

class TestManifestStores:
    def test_local_store_lists_root_files(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "x"}')
        (tmp_path / "README.md").write_text("# readme")
        (tmp_path / "src").mkdir()
        store = LocalManifestStore(tmp_path)
        assert store.files() == ["README.md", "package.json"]

    @pytest.mark.asyncio
    async def test_local_store_reads_content(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        store = LocalManifestStore(tmp_path)
        assert await store.read("go.mod") == "module example.com/foo\n"
        assert await store.read("missing.toml") is None

    def test_remote_store_lists_root_files(self):
        async def reader(name: str) -> str | None:
            return None

        store = RemoteManifestStore(["package.json", "mygem.gemspec"], reader=reader)
        assert store.files() == ["package.json", "mygem.gemspec"]

    @pytest.mark.asyncio
    async def test_remote_store_reads_through_reader(self):
        async def reader(name: str):
            return '{"name": "x"}' if name == "package.json" else None

        store = RemoteManifestStore(["package.json"], reader=reader)
        assert await store.read("package.json") == '{"name": "x"}'
        assert await store.read("pyproject.toml") is None

    @pytest.mark.asyncio
    async def test_remote_store_swallows_reader_errors(self):
        async def reader(name: str):
            raise RuntimeError("boom")

        store = RemoteManifestStore(["package.json"], reader=reader)
        assert await store.read("package.json") is None


# ---------------------------------------------------------------------------
# Ecosystem detection
# ---------------------------------------------------------------------------

class TestDetectEcosystems:
    """_detect_ecosystems works on a root file list (local or remote)."""

    def test_npm_project(self):
        assert _detect_ecosystems(["package.json"]) == ["npm"]

    def test_python_project(self):
        assert _detect_ecosystems(["pyproject.toml"]) == ["pypi"]

    def test_rust_project(self):
        assert _detect_ecosystems(["Cargo.toml"]) == ["crates.io"]

    def test_go_project(self):
        assert _detect_ecosystems(["go.mod"]) == ["go"]

    def test_case_insensitive_dockerfile(self):
        # Remote root files are lowercased by fetch_community_files.
        assert _detect_ecosystems(["dockerfile"]) == ["docker"]

    def test_gemspec_glob(self):
        assert _detect_ecosystems(["mygem.gemspec"]) == ["rubygems"]

    def test_no_manifests(self):
        assert _detect_ecosystems(["README.md"]) == []

    def test_empty_list(self):
        assert _detect_ecosystems([]) == []

    def test_none(self):
        assert _detect_ecosystems(None) == []


# ---------------------------------------------------------------------------
# Package name extraction from manifest content
# ---------------------------------------------------------------------------

class TestExtractPackageName:
    """Package names must come from config files, NOT from the repo name."""

    def test_npm_from_package_json(self):
        assert _extract_npm_package_name(json.dumps({"name": "@scope/my-lib"})) == "@scope/my-lib"

    def test_npm_missing(self):
        assert _extract_npm_package_name("{}") is None

    def test_npm_invalid_json(self):
        assert _extract_npm_package_name("not json") is None

    def test_python_from_pyproject(self):
        assert _extract_pyproject_name('[project]\nname = "my-project"') == "my-project"

    def test_pyproject_no_name(self):
        assert _extract_pyproject_name('[project]\nversion = "1.0"') is None

    def test_python_from_setup_cfg(self):
        assert _extract_setup_cfg_name("[metadata]\nname = mypkg") == "mypkg"

    def test_setup_cfg_no_name(self):
        assert _extract_setup_cfg_name("[metadata]\nversion = 1.0") is None

    def test_crate_from_cargo_toml(self):
        assert _extract_crate_name('[package]\nname = "mycrate"') == "mycrate"

    def test_go_module_path(self):
        assert _extract_go_module_path("module github.com/acme/widgets\n") == "github.com/acme/widgets"

    def test_gem_from_gemspec(self):
        assert _extract_gem_name('spec.name = "mygem"') == "mygem"

    def test_maven_coordinates(self):
        content = (
            "<project><groupId>com.acme</groupId>"
            "<artifactId>widgets</artifactId></project>"
        )
        assert _extract_maven_coordinates(content) == "com.acme:widgets"

    def test_docker_image_from_compose(self):
        content = "services:\n  web:\n    image: nginx:latest\n"
        assert _extract_docker_image_name(content) == "nginx:latest"

    @pytest.mark.asyncio
    async def test_dispatcher_routes(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "x"}')
        store = LocalManifestStore(tmp_path)
        assert await _extract_package_name(store, "npm") == "x"

    @pytest.mark.asyncio
    async def test_dispatcher_unknown_ecosystem(self, tmp_path):
        store = LocalManifestStore(tmp_path)
        assert await _extract_package_name(store, "unknown-eco") is None

    @pytest.mark.asyncio
    async def test_dispatcher_pyproject_before_setup_cfg(self, tmp_path):
        # pyproject.toml wins over setup.cfg even when both exist.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "modern"')
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = legacy")
        store = LocalManifestStore(tmp_path)
        assert await _extract_package_name(store, "pypi") == "modern"

    @pytest.mark.asyncio
    async def test_dispatcher_missing_manifest(self, tmp_path):
        (tmp_path / "README.md").write_text("x")
        store = LocalManifestStore(tmp_path)
        assert await _extract_package_name(store, "npm") is None


# ---------------------------------------------------------------------------
# Language fallback (remote listing unavailable)
# ---------------------------------------------------------------------------

class TestLanguageFallback:
    @pytest.mark.asyncio
    async def test_probes_candidates_from_primary_language(self):
        repo = _make_repo()
        repo.languages.languages = {"Python": 100}

        async def reader(name: str):
            return '[project]\nname = "pyproj"' if name == "pyproject.toml" else None

        store = RemoteManifestStore([], reader=reader)
        assert await _collect_ecosystems(store, repo) == ["pypi"]

    @pytest.mark.asyncio
    async def test_no_probe_when_listing_found(self):
        repo = _make_repo()
        repo.languages.languages = {"Python": 100}

        async def reader(name: str):
            return "{}"  # a listing exists: language is ignored

        store = RemoteManifestStore(["package.json"], reader=reader)
        assert await _collect_ecosystems(store, repo) == ["npm"]

    @pytest.mark.asyncio
    async def test_no_probe_for_unknown_language(self):
        repo = _make_repo()
        repo.languages.languages = {"COBOL": 100}

        async def reader(name: str):
            return "x"

        store = RemoteManifestStore([], reader=reader)
        assert await _collect_ecosystems(store, repo) == []

    @pytest.mark.asyncio
    async def test_no_probe_when_manifest_absent(self):
        repo = _make_repo()
        repo.languages.languages = {"Go": 100}

        async def reader(name: str):
            return None

        store = RemoteManifestStore([], reader=reader)
        assert await _collect_ecosystems(store, repo) == []


# ---------------------------------------------------------------------------
# Registry info fetching — no inference from repo name
# ---------------------------------------------------------------------------

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
    async def test_remote_mode_uses_reader(self):
        """Remote mode: manifests are read through the reader, the root
        listing comes from community.root_files."""
        repo = _make_repo()
        repo.community.root_files = ["package.json"]
        cache = Cache(str(Path("/tmp/gh-score-test-cache-remote")))

        async def reader(name: str):
            return json.dumps({"name": "left-pad"})

        mock_response = _mock_http_response(200, {
            "name": "left-pad",
            "dist-tags": {"latest": "1.3.0"},
            "time": {},
            "versions": {},
        })

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(mock_response, mock_response)
            result = await fetch_registry_info(
                repo, local_path=None, cache=cache, remote_reader=reader
            )

        assert len(result) == 1
        assert result[0].ecosystem == "npm"
        assert result[0].package_name == "left-pad"

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
        """When package.json exists but has no "name" field, no package name
        can be extracted.  The ecosystem must be skipped entirely — the code
        must NOT fall back to inferring the name from the repo name."""
        (tmp_path / "package.json").write_text("{}")
        repo = _make_repo(owner="acme", repo="widgets")
        cache = Cache(str(tmp_path))

        result = await fetch_registry_info(
            repo, local_path=str(tmp_path), cache=cache
        )

        assert result == []


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

    def test_pypi_license_full_text_reduced_to_first_line(self):
        """The legacy license field may carry the whole license text; only
        the first line (the license name) is kept."""
        data = {
            "info": {
                "version": "1.0.0",
                "license": (
                    "MIT License\n\n"
                    "Copyright (c) 2026 Someone\n\n"
                    "Permission is hereby granted, free of charge, ..."
                ),
            },
            "releases": {},
        }
        info = _parse_pypi_response(data, RegistryInfo(ecosystem="pypi", package_name="x"))
        assert info.registry_license == "MIT License"

    def test_pypi_license_expression_preferred(self):
        data = {
            "info": {
                "version": "1.0.0",
                "license_expression": "MIT",
                "license": "MIT License\n\nCopyright ...",
            },
            "releases": {},
        }
        info = _parse_pypi_response(data, RegistryInfo(ecosystem="pypi", package_name="x"))
        assert info.registry_license == "MIT"

    def test_pypi_license_from_classifier(self):
        data = {
            "info": {
                "version": "1.0.0",
                "classifiers": [
                    "Programming Language :: Python :: 3",
                    "License :: OSI Approved :: Apache Software License",
                ],
                "license": "",
            },
            "releases": {},
        }
        info = _parse_pypi_response(data, RegistryInfo(ecosystem="pypi", package_name="x"))
        assert info.registry_license == "Apache Software License"

    def test_pypi_no_license(self):
        data = {"info": {"version": "1.0.0"}, "releases": {}}
        info = _parse_pypi_response(data, RegistryInfo(ecosystem="pypi", package_name="x"))
        assert info.registry_license is None

    def test_go_license_dict_type_extracted(self):
        """pkg.go.dev may express the license as {"type", "filePath"}."""
        data = {
            "module": {
                "latestVersion": "v1.0.0",
                "license": {"type": "BSD-3-Clause", "filePath": "LICENSE"},
            },
        }
        info = _parse_go_response(data, RegistryInfo(ecosystem="go", package_name="x"))
        assert info.registry_license == "BSD-3-Clause"

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
    async def test_crates_dependents_meta_total(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"dependencies": [], "meta": {"total": 30758}})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(resp)
            mock_cls.return_value = instance
            count = await _fetch_crates_dependents("serde", cache)

        assert count == 30758
        url = instance.get.await_args.args[0]
        assert "reverse_dependencies" in url

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
    async def test_go_imported_by_total(self, tmp_path):
        """The pkg.go.dev response nests items and carries the exact total."""
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {
            "modulePath": "github.com/gorilla/mux",
            "importedBy": {"items": ["a"], "total": 99459},
        })
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            count = await _fetch_go_imported_by("github.com/gorilla/mux", cache)
        assert count == 99459

    @pytest.mark.asyncio
    async def test_go_imported_by_flat_list_fallback(self, tmp_path):
        """Legacy flat-array shape is still accepted."""
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
    async def test_rubygems_dependents_array_length(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, ["gem-a", "gem-b", "gem-c"])
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            count = await _fetch_rubygems_dependents("rake", cache)
        assert count == 3

    @pytest.mark.asyncio
    async def test_rubygems_dependents_non_list(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"error": "boom"})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            count = await _fetch_rubygems_dependents("rake", cache)
        assert count is None

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
        # Maven timestamps are epoch ms; parsed as aware UTC
        assert info.latest_date is not None
        assert info.latest_date == datetime(2023, 1, 1, tzinfo=timezone.utc)
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
# libraries.io dependents
# ---------------------------------------------------------------------------

class TestLibrariesIODependents:
    @pytest.mark.asyncio
    async def test_requires_key(self, tmp_path):
        cache = Cache(str(tmp_path))
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            count = await _fetch_libraries_io_dependents("pypi", "foo", "", cache)
        assert count is None
        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_platform(self, tmp_path):
        cache = Cache(str(tmp_path))
        count = await _fetch_libraries_io_dependents("docker", "foo", "key", cache)
        assert count is None

    @pytest.mark.asyncio
    async def test_pypi_dependents_count(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"dependents_count": 7, "rank": 3})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(resp)
            mock_cls.return_value = instance
            count = await _fetch_libraries_io_dependents("pypi", "requests", "key", cache)

        assert count == 7
        url = instance.get.await_args.args[0]
        assert "/api/Pypi/requests" in url
        kwargs = instance.get.await_args.kwargs
        assert kwargs["params"]["api_key"] == "key"

    @pytest.mark.asyncio
    async def test_scoped_npm_keeps_slash(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"dependents_count": 3})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(resp)
            mock_cls.return_value = instance
            count = await _fetch_libraries_io_dependents("npm", "@babel/core", "key", cache)

        assert count == 3
        url = instance.get.await_args.args[0]
        assert "/api/NPM/@babel/core" in url

    @pytest.mark.asyncio
    async def test_maven_colon_encoded(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"dependents_count": 12})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            instance = _mock_async_client(resp)
            mock_cls.return_value = instance
            count = await _fetch_libraries_io_dependents(
                "maven", "com.acme:widgets", "key", cache
            )

        assert count == 12
        url = instance.get.await_args.args[0]
        assert "com.acme%3Awidgets" in url

    @pytest.mark.asyncio
    async def test_missing_field_returns_none(self, tmp_path):
        cache = Cache(str(tmp_path))
        resp = _mock_http_response(200, {"name": "foo"})
        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(resp)
            count = await _fetch_libraries_io_dependents("pypi", "foo", "key", cache)
        assert count is None


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

    @pytest.mark.asyncio
    async def test_dependents_wired_for_go(self, tmp_path):
        """Go dependents (imported-by) are fetched and attached."""
        (tmp_path / "go.mod").write_text("module example.com/mod\n")
        repo = _make_repo()
        cache = Cache(str(tmp_path))

        module_resp = _mock_http_response(200, {
            "module": {"latestVersion": "v1.0.0", "license": "MIT"},
        })
        imported_resp = _mock_http_response(200, {
            "importedBy": {"items": ["a"], "total": 99},
        })

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(module_resp, imported_resp)
            result = await fetch_registry_info(repo, local_path=str(tmp_path), cache=cache)

        assert len(result) == 1
        assert result[0].ecosystem == "go"
        assert result[0].dependents == 99

    @pytest.mark.asyncio
    async def test_libraries_io_wired_for_pypi(self, tmp_path):
        """PyPI dependents come from libraries.io when a key is set."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "pylib"')
        repo = _make_repo()
        cache = Cache(str(tmp_path))

        pypi_resp = _mock_http_response(200, {"info": {"version": "2.0.0"}, "releases": {}})
        pypi_dl = _mock_http_response(200, {"data": {"last_month": 20}})
        libio_resp = _mock_http_response(200, {"dependents_count": 5})

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(pypi_resp, pypi_dl, libio_resp)
            result = await fetch_registry_info(
                repo,
                local_path=str(tmp_path),
                cache=cache,
                libraries_io_key="secret",
            )

        assert len(result) == 1
        assert result[0].dependents == 5

    @pytest.mark.asyncio
    async def test_libraries_io_skipped_without_key(self, tmp_path):
        """No key: PyPI dependents stays None, no extra HTTP call."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "pylib"')
        repo = _make_repo()
        cache = Cache(str(tmp_path))

        pypi_resp = _mock_http_response(200, {"info": {"version": "2.0.0"}, "releases": {}})
        pypi_dl = _mock_http_response(200, {"data": {"last_month": 20}})

        with patch("gh_score.core.fetchers.registries.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(pypi_resp, pypi_dl)
            result = await fetch_registry_info(repo, local_path=str(tmp_path), cache=cache)

        assert len(result) == 1
        assert result[0].dependents is None


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

    def test_match_normalizes_trailing_word(self):
        """Registry "MIT License" must match the GitHub SPDX id "MIT"."""
        reg = self._make_registry("MIT License")
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
