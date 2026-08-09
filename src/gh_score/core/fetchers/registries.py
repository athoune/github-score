"""Package registry fetcher.

Detects ecosystem and queries package registries for metadata.
Supports: PyPI, npm, crates.io, Go (pkg.go.dev), RubyGems, Maven Central, Docker Hub.

Popularity metrics per package:
- downloads: total or recent, depending on the ecosystem (SPECS §6.3.9);
- dependents (reverse dependencies): exact count from the official
  registries (crates.io, RubyGems, pkg.go.dev) or from the third-party
  libraries.io aggregator (PyPI, npm, Maven) when an API key is configured.

Manifests are read through a store abstraction: ``LocalManifestStore`` reads
a local clone from disk; ``RemoteManifestStore`` lists root files via
``community.root_files`` and reads their content through the GitHub contents
API, so registry detection also works without a local clone (SPECS §6.3.7).
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol
from urllib.parse import quote

import httpx
from platformdirs import user_cache_dir

from gh_score.core.cache import Cache
from gh_score.core.models import RegistryInfo, Repository


# Ecosystem detection patterns
_ECOSYSTEM_PATTERNS = {
    "pypi": ["pyproject.toml", "setup.py", "setup.cfg"],
    "npm": ["package.json"],
    "crates.io": ["Cargo.toml"],
    "go": ["go.mod"],
    "maven": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "rubygems": ["*.gemspec"],
    "docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
}

# Candidate root manifests probed from the primary language when the remote
# root listing is unavailable (SPECS §6.3.7). Glob patterns are skipped by
# the probe: they need a listing to expand.
_LANGUAGE_MANIFESTS = {
    "python": ["pyproject.toml", "setup.py", "setup.cfg"],
    "javascript": ["package.json"],
    "typescript": ["package.json"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "kotlin": ["pom.xml", "build.gradle.kts", "build.gradle"],
    "groovy": ["pom.xml", "build.gradle"],
    "dockerfile": ["Dockerfile"],
}


class ManifestStore(Protocol):
    """Reads package manifests from a local clone or a remote repository."""

    def files(self) -> list[str]:
        """Root file names (case preserved as found)."""
        ...

    async def read(self, name: str) -> str | None:
        """Read a file's decoded text content, or None when absent."""
        ...


class LocalManifestStore:
    """Manifest store backed by a local clone on disk."""

    def __init__(self, repo_path: Path | str) -> None:
        self._path = Path(repo_path)

    def files(self) -> list[str]:
        if not self._path.is_dir():
            return []
        return sorted(p.name for p in self._path.iterdir() if p.is_file())

    async def read(self, name: str) -> str | None:
        candidate = self._path / name
        if not candidate.is_file():
            return None
        try:
            return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


class RemoteManifestStore:
    """Manifest store backed by the GitHub contents API.

    ``root_files`` is the root listing already fetched by
    ``fetch_community_files`` (lowercased); ``reader`` fetches a file's
    decoded content (``GitHubFetcher.fetch_file_content`` bound to the
    repository URL).
    """

    def __init__(
        self,
        root_files: Iterable[str],
        reader: Callable[[str], Awaitable[str | None]],
    ) -> None:
        self._root_files = list(root_files)
        self._reader = reader

    def files(self) -> list[str]:
        return list(self._root_files)

    async def read(self, name: str) -> str | None:
        try:
            return await self._reader(name)
        except Exception:
            return None


def _name_matches(filename: str, pattern: str) -> bool:
    """Case-insensitive match of a root filename against a manifest pattern."""
    if "*" in pattern:
        return fnmatch(filename.lower(), pattern.lower())
    return filename.lower() == pattern.lower()


def _ecosystem_for_manifest(manifest: str) -> str | None:
    """Ecosystem that owns a concrete manifest name (fallback probing)."""
    lower = manifest.lower()
    for ecosystem, patterns in _ECOSYSTEM_PATTERNS.items():
        for pattern in patterns:
            if "*" in pattern:
                continue
            if lower == pattern.lower():
                return ecosystem
    return None


def _detect_ecosystems(files: Iterable[str] | None) -> list[str]:
    """Detect which ecosystems the project belongs to based on root manifests."""
    if not files:
        return []

    names = {name.lower() for name in files}
    ecosystems = []
    for ecosystem, patterns in _ECOSYSTEM_PATTERNS.items():
        for pattern in patterns:
            lower_pattern = pattern.lower()
            if "*" in lower_pattern:
                if any(fnmatch(name, lower_pattern) for name in names):
                    ecosystems.append(ecosystem)
                    break
            elif lower_pattern in names:
                ecosystems.append(ecosystem)
                break
    return ecosystems


async def _collect_ecosystems(store: ManifestStore, repo: Repository) -> list[str]:
    """Detect ecosystems from the manifest listing, falling back to probing
    candidate manifests from the primary language when the listing is empty
    (remote mode with an unavailable contents listing, SPECS §6.3.7)."""
    ecosystems = _detect_ecosystems(store.files())
    if ecosystems:
        return ecosystems

    primary = (repo.languages.primary or "").lower() if repo.languages else ""
    for pattern in _LANGUAGE_MANIFESTS.get(primary, []):
        if "*" in pattern:
            continue  # glob needs a listing; cannot probe a concrete name
        if await store.read(pattern) is not None:
            ecosystem = _ecosystem_for_manifest(pattern)
            if ecosystem and ecosystem not in ecosystems:
                ecosystems.append(ecosystem)
    return ecosystems


def _extract_pyproject_name(content: str) -> str | None:
    """Extract the package name from pyproject.toml content ([project].name)."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None
    return data.get("project", {}).get("name")


def _extract_setup_cfg_name(content: str) -> str | None:
    """Extract the package name from setup.cfg content."""
    match = re.search(r"name\s*=\s*(.+)", content)
    return match.group(1).strip() if match else None


def _extract_npm_package_name(content: str) -> str | None:
    """Extract npm package name from package.json content."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    return data.get("name") or None


def _extract_crate_name(content: str) -> str | None:
    """Extract Rust crate name from Cargo.toml content."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None
    return data.get("package", {}).get("name")


def _extract_go_module_path(content: str) -> str | None:
    """Extract Go module path from go.mod content."""
    for line in content.splitlines():
        if line.startswith("module "):
            parts = line.split()
            return parts[1].strip() if len(parts) > 1 else None
    return None


def _extract_gem_name(content: str) -> str | None:
    """Extract Ruby gem name from .gemspec content."""
    match = re.search(r'\.name\s*=\s*["\']([^"\']+)["\']', content)
    return match.group(1) if match else None


def _extract_maven_coordinates(content: str) -> str | None:
    """Extract Maven groupId:artifactId from pom.xml content."""
    group_match = re.search(r"<groupId>([^<]+)</groupId>", content)
    artifact_match = re.search(r"<artifactId>([^<]+)</artifactId>", content)
    if group_match and artifact_match:
        return f"{group_match.group(1)}:{artifact_match.group(1)}"
    return None


def _extract_docker_image_name(content: str) -> str | None:
    """Extract Docker image name from docker-compose content."""
    match = re.search(r"image:\s*(\S+)", content)
    return match.group(1) if match else None


# Manifest pattern → extractor, in probe order per ecosystem.
_MANIFEST_EXTRACTORS: dict[str, dict[str, Callable[[str], str | None]]] = {
    "pypi": {
        "pyproject.toml": _extract_pyproject_name,
        "setup.cfg": _extract_setup_cfg_name,
    },
    "npm": {"package.json": _extract_npm_package_name},
    "crates.io": {"Cargo.toml": _extract_crate_name},
    "go": {"go.mod": _extract_go_module_path},
    "rubygems": {"*.gemspec": _extract_gem_name},
    "maven": {"pom.xml": _extract_maven_coordinates},
    "docker": {
        "docker-compose.yml": _extract_docker_image_name,
        "docker-compose.yaml": _extract_docker_image_name,
    },
}


async def _extract_package_name(store: ManifestStore, ecosystem: str) -> str | None:
    """Extract the package name from the first matching manifest of the ecosystem."""
    extractors = _MANIFEST_EXTRACTORS.get(ecosystem)
    if not extractors:
        return None
    files = store.files()
    for pattern, extractor in extractors.items():
        candidates = [name for name in files if _name_matches(name, pattern)]
        if not candidates:
            continue
        content = await store.read(candidates[0])
        if content is None:
            continue
        name = extractor(content)
        if name:
            return name
    return None


# ---------------------------------------------------------------------------
# PyPI
# ---------------------------------------------------------------------------

async def _fetch_pypi(package_name: str, cache: Cache) -> RegistryInfo:
    """Fetch package info from PyPI including download stats."""
    info = RegistryInfo(ecosystem="pypi", package_name=package_name)

    cache_key = f"pypi:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_pypi_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://pypi.org/pypi/{package_name}/json")
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_pypi_response(data, info)
    except Exception:
        pass

    return info


def _parse_pypi_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse PyPI API response."""
    info.exists = True

    pkg_info = data.get("info", {})
    info.latest_version = pkg_info.get("version")
    info.registry_license = pkg_info.get("license")

    # Upload time for latest release
    releases = data.get("releases", {})
    if info.latest_version and info.latest_version in releases:
        version_files = releases[info.latest_version]
        if version_files:
            upload_time = version_files[0].get("upload_time_iso_8601")
            if upload_time:
                try:
                    info.latest_date = datetime.fromisoformat(
                        upload_time.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

    # Check for deprecated/yanked status
    if info.latest_version and info.latest_version in releases:
        for file_info in releases[info.latest_version]:
            if file_info.get("yanked"):
                info.deprecated = True
                break

    return info


async def _fetch_pypi_downloads(package_name: str, cache: Cache) -> int | None:
    """Fetch download stats from PyPIstats API."""
    cache_key = f"pypistats:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("downloads")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://pypistats.org/api/packages/{package_name}/recent"
            )
            if resp.status_code == 200:
                data = resp.json()
                downloads = data.get("data", {}).get("last_month", 0)
                cache.set_json(cache_key, {"downloads": downloads}, ttl_seconds=86400)
                return downloads
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------

async def _fetch_npm(package_name: str, cache: Cache) -> RegistryInfo:
    """Fetch package info from npm including download stats."""
    info = RegistryInfo(ecosystem="npm", package_name=package_name)

    cache_key = f"npm:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_npm_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Handle scoped packages: @scope/name -> @scope%2Fname
            encoded_name = package_name.replace("/", "%2F")
            url = f"https://registry.npmjs.org/{encoded_name}"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_npm_response(data, info)
    except Exception:
        pass

    return info


def _parse_npm_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse npm registry API response."""
    info.exists = True
    info.latest_version = data.get("dist-tags", {}).get("latest")

    # Get time of latest version
    times = data.get("time", {})
    if info.latest_version and info.latest_version in times:
        try:
            time_str = times[info.latest_version]
            info.latest_date = datetime.fromisoformat(
                time_str.replace("Z", "+00:00")
            )
        except ValueError:
            pass

    # Check deprecated flag on latest version
    versions = data.get("versions", {})
    if info.latest_version and info.latest_version in versions:
        version_data = versions[info.latest_version]
        info.deprecated = version_data.get("deprecated", False) is not False

    return info


async def _fetch_npm_downloads(package_name: str, cache: Cache) -> int | None:
    """Fetch download stats from npm downloads API."""
    cache_key = f"npm-downloads:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("downloads")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            encoded_name = package_name.replace("/", "%2F")
            resp = await client.get(
                f"https://api.npmjs.org/downloads/point/last-month/{encoded_name}"
            )
            if resp.status_code == 200:
                data = resp.json()
                downloads = data.get("downloads", 0)
                cache.set_json(cache_key, {"downloads": downloads}, ttl_seconds=86400)
                return downloads
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# crates.io
# ---------------------------------------------------------------------------

async def _fetch_crates(package_name: str, cache: Cache) -> RegistryInfo:
    """Fetch crate info from crates.io."""
    info = RegistryInfo(ecosystem="crates.io", package_name=package_name)

    cache_key = f"crates:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_crates_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://crates.io/api/v1/crates/{package_name}",
                headers={"User-Agent": "gh-score/0.1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_crates_response(data, info)
    except Exception:
        pass

    return info


def _parse_crates_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse crates.io API response."""
    crate = data.get("crate", {})
    if crate:
        info.exists = True
        info.latest_version = crate.get("newest_version") or crate.get("max_version")
        info.downloads = crate.get("downloads")
        info.recent_downloads = crate.get("recent_downloads")
        info.registry_license = crate.get("license")

        # Updated date
        updated = crate.get("updated_at")
        if updated:
            try:
                info.latest_date = datetime.fromisoformat(
                    updated.replace("Z", "+00:00")
                )
            except ValueError:
                pass

    return info


async def _fetch_crates_dependents(crate_name: str, cache: Cache) -> int | None:
    """Fetch the number of crates depending on this crate (reverse dependencies).

    ``meta.total`` carries the exact count; ``per_page=1`` keeps the payload
    small while preserving it.
    """
    cache_key = f"crates-dependents:{crate_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("count")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://crates.io/api/v1/crates/{crate_name}/reverse_dependencies?per_page=1",
                headers={"User-Agent": "gh-score/0.1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("meta", {}).get("total")
                if isinstance(total, int):
                    cache.set_json(cache_key, {"count": total}, ttl_seconds=7 * 86400)
                    return total
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Go (pkg.go.dev)
# ---------------------------------------------------------------------------

async def _fetch_go(module_path: str, cache: Cache) -> RegistryInfo:
    """Fetch module info from pkg.go.dev."""
    info = RegistryInfo(ecosystem="go", package_name=module_path)

    cache_key = f"go:{module_path}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_go_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://pkg.go.dev/v1beta/module/{module_path}"
            )
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_go_response(data, info)
    except Exception:
        pass

    return info


def _parse_go_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse pkg.go.dev module API response."""
    module = data.get("module", {})
    if module:
        info.exists = True
        info.latest_version = module.get("latestVersion")
        info.registry_license = module.get("license")

        # Version timestamp
        updated = module.get("updatedAt")
        if updated:
            try:
                info.latest_date = datetime.fromisoformat(
                    updated.replace("Z", "+00:00")
                )
            except ValueError:
                pass

    # Check for imported-by count as a popularity proxy
    versions = data.get("versions", [])
    if versions:
        info.latest_version = info.latest_version or versions[0].get("version")

    return info


async def _fetch_go_imported_by(module_path: str, cache: Cache) -> int | None:
    """Fetch the number of packages importing this module (popularity proxy).

    The pkg.go.dev response nests the list under ``importedBy.items`` and
    carries the exact total in ``importedBy.total`` (the legacy flat-array
    shape is still accepted defensively).
    """
    cache_key = f"go-imported-by:{module_path}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("count")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://pkg.go.dev/v1beta/imported-by/{module_path}?limit=1"
            )
            if resp.status_code == 200:
                data = resp.json()
                imported = data.get("importedBy", {}) if isinstance(data, dict) else {}
                if isinstance(imported, dict):
                    total = imported.get("total")
                    count = total if isinstance(total, int) else len(imported.get("items", []))
                elif isinstance(imported, list):
                    count = len(imported)
                else:
                    return None
                cache.set_json(cache_key, {"count": count}, ttl_seconds=7 * 86400)
                return count
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# RubyGems
# ---------------------------------------------------------------------------

async def _fetch_rubygems(gem_name: str, cache: Cache) -> RegistryInfo:
    """Fetch gem info from RubyGems."""
    info = RegistryInfo(ecosystem="rubygems", package_name=gem_name)

    cache_key = f"rubygems:{gem_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_rubygems_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://rubygems.org/api/v1/gems/{gem_name}.json"
            )
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_rubygems_response(data, info)
    except Exception:
        pass

    return info


def _parse_rubygems_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse RubyGems API response."""
    if data.get("name"):
        info.exists = True
        info.latest_version = data.get("version")
        info.downloads = data.get("downloads")
        info.recent_downloads = data.get("version_downloads")

        # License (SPDX string or array)
        license_data = data.get("license")
        if isinstance(license_data, list):
            info.registry_license = ", ".join(license_data)
        elif isinstance(license_data, str):
            info.registry_license = license_data

        # Updated date
        updated = data.get("updated_at")
        if updated:
            try:
                info.latest_date = datetime.fromisoformat(
                    updated.replace("Z", "+00:00")
                )
            except ValueError:
                pass

        # Deprecated check
        if data.get("yanked"):
            info.deprecated = True

    return info


async def _fetch_rubygems_dependents(gem_name: str, cache: Cache) -> int | None:
    """Fetch the number of gems depending on this gem (reverse dependencies).

    The endpoint ignores pagination and returns the whole array in one
    response; the count is its length. It may be capped by RubyGems for very
    popular gems, so treat it as an approximate floor.
    """
    cache_key = f"rubygems-dependents:{gem_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("count")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://rubygems.org/api/v1/gems/{gem_name}/reverse_dependencies.json"
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    cache.set_json(
                        cache_key, {"count": len(data)}, ttl_seconds=7 * 86400
                    )
                    return len(data)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Maven Central
# ---------------------------------------------------------------------------

async def _fetch_maven(group_artifact: str, cache: Cache) -> RegistryInfo:
    """Fetch artifact info from Maven Central."""
    info = RegistryInfo(ecosystem="maven", package_name=group_artifact)

    cache_key = f"maven:{group_artifact}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_maven_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Parse group:artifact format
            if ":" in group_artifact:
                group, artifact = group_artifact.split(":", 1)
            else:
                return info

            url = (
                "https://search.maven.org/solrsearch/select"
                f"?q=g:{group}+AND+a:{artifact}&rows=1&wt=json"
            )
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_maven_response(data, info)
    except Exception:
        pass

    return info


def _parse_maven_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse Maven Central API response."""
    response = data.get("response", {})
    docs = response.get("docs", [])
    if docs:
        doc = docs[0]
        info.exists = True
        info.latest_version = doc.get("latestVersion")
        info.downloads = doc.get("downloadCount")

        # Timestamp (epoch ms → aware UTC, consistent with the other parsers)
        timestamp = doc.get("timestamp")
        if timestamp:
            try:
                info.latest_date = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                pass

    return info


# ---------------------------------------------------------------------------
# Docker Hub
# ---------------------------------------------------------------------------

async def _fetch_docker(image_name: str, cache: Cache) -> RegistryInfo:
    """Fetch image info from Docker Hub."""
    info = RegistryInfo(ecosystem="docker", package_name=image_name)

    cache_key = f"docker:{image_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_docker_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Docker Hub API: namespace/name
            if "/" in image_name:
                namespace, name = image_name.split("/", 1)
            else:
                namespace = "library"  # Official images
                name = image_name

            url = f"https://hub.docker.com/v2/repositories/{namespace}/{name}"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)
                return _parse_docker_response(data, info)
    except Exception:
        pass

    return info


def _parse_docker_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse Docker Hub API response."""
    if data.get("name"):
        info.exists = True
        info.downloads = data.get("pull_count")

        # Last updated
        updated = data.get("last_updated")
        if updated:
            try:
                info.latest_date = datetime.fromisoformat(
                    updated.replace("Z", "+00:00")
                )
            except ValueError:
                pass

        # Check if the repository is marked as inactive
        if data.get("is_private"):
            info.deprecated = True

    return info


# ---------------------------------------------------------------------------
# libraries.io (third-party dependents for PyPI / npm / Maven)
# ---------------------------------------------------------------------------

# libraries.io platform names for our ecosystems (SPECS §6.3.8).
_LIBRARIES_IO_PLATFORMS = {
    "pypi": "Pypi",
    "npm": "NPM",
    "maven": "Maven",
}


async def _fetch_libraries_io_dependents(
    ecosystem: str,
    package_name: str,
    api_key: str,
    cache: Cache,
) -> int | None:
    """Fetch dependents from libraries.io when the official registry exposes none.

    Third-party aggregator (Tidelift/Sonar); used only for PyPI, npm and
    Maven. Requires a free API key; without one (or on any failure) returns
    None so the feature degrades silently.
    """
    platform = _LIBRARIES_IO_PLATFORMS.get(ecosystem)
    if not platform or not api_key:
        return None

    # Project names keep their scopes/slashes (npm @scope/name, Maven
    # group:artifact); the colon is URL-encoded.
    encoded = quote(package_name, safe="/@")
    cache_key = f"librariesio-dependents:{ecosystem}:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("count")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://libraries.io/api/{platform}/{encoded}",
                params={"api_key": api_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                count = data.get("dependents_count")
                if isinstance(count, int):
                    cache.set_json(cache_key, {"count": count}, ttl_seconds=7 * 86400)
                    return count
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# pylint: disable=too-many-branches
# mccabe: MC0001
async def _fetch_ecosystem(
    cache: Cache,
    ecosystem: str,
    package_name: str,
    libraries_io_key: str,
) -> RegistryInfo:
    """Fetch metadata, downloads and dependents for a single ecosystem."""
    if ecosystem == "pypi":
        info = await _fetch_pypi(package_name, cache)
        info.downloads = await _fetch_pypi_downloads(package_name, cache)
        info.dependents = await _fetch_libraries_io_dependents(
            "pypi", package_name, libraries_io_key, cache
        )
    elif ecosystem == "npm":
        info = await _fetch_npm(package_name, cache)
        info.recent_downloads = await _fetch_npm_downloads(package_name, cache)
        info.dependents = await _fetch_libraries_io_dependents(
            "npm", package_name, libraries_io_key, cache
        )
    elif ecosystem == "crates.io":
        info = await _fetch_crates(package_name, cache)
        info.dependents = await _fetch_crates_dependents(package_name, cache)
    elif ecosystem == "go":
        info = await _fetch_go(package_name, cache)
        info.dependents = await _fetch_go_imported_by(package_name, cache)
    elif ecosystem == "rubygems":
        info = await _fetch_rubygems(package_name, cache)
        info.dependents = await _fetch_rubygems_dependents(package_name, cache)
    elif ecosystem == "maven":
        info = await _fetch_maven(package_name, cache)
        info.dependents = await _fetch_libraries_io_dependents(
            "maven", package_name, libraries_io_key, cache
        )
    else:
        info = await _fetch_docker(package_name, cache)
    return info


async def fetch_registry_info(
    repo: Repository,
    local_path: str | None = None,
    cache: Cache | None = None,
    remote_reader: Callable[[str], Awaitable[str | None]] | None = None,
    libraries_io_key: str = "",
) -> list[RegistryInfo]:
    """Detect and fetch package registry information.

    Args:
        repo: Repository model (language fallback, license comparison).
        local_path: Path to a local clone (manifest detection from the
            filesystem).
        cache: Cache instance for HTTP responses.
        remote_reader: Async callable fetching a root file's decoded content
            (GitHub contents API). Enables remote detection without a clone;
            the root listing comes from ``repo.community.root_files``.
        libraries_io_key: libraries.io API key for dependents on ecosystems
            whose official registry exposes none (PyPI, npm, Maven).

    Returns:
        List of RegistryInfo for each detected ecosystem.
    """
    if cache is None:
        cache = Cache(str(Path(user_cache_dir("gh-score")) / "cache"))

    if local_path is not None:
        store: ManifestStore = LocalManifestStore(local_path)
    elif remote_reader is not None:
        store = RemoteManifestStore(repo.community.root_files, remote_reader)
    else:
        return []

    ecosystems = await _collect_ecosystems(store, repo)
    if not ecosystems:
        return []

    results: list[RegistryInfo] = []
    for ecosystem in ecosystems:
        package_name = await _extract_package_name(store, ecosystem)
        if not package_name:
            # No manifest file or no name inside it — skip this ecosystem
            # rather than guessing from the repository name (risk of homonyms).
            continue
        results.append(
            await _fetch_ecosystem(cache, ecosystem, package_name, libraries_io_key)
        )

    # Compare registry licenses with GitHub license
    _compare_licenses(results, repo)

    return results


def _compare_licenses(registries: list[RegistryInfo], repo: Repository) -> None:
    """Compare registry-declared licenses with GitHub-detected license."""
    github_license = repo.license.spdx_id
    if not github_license:
        return

    for reg in registries:
        if reg.registry_license and reg.exists:
            # Normalize for comparison
            reg_license = reg.registry_license.upper().strip()
            gh_license = github_license.upper().strip()
            reg.license_matches_github = reg_license == gh_license
