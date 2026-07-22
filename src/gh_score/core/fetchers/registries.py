"""Package registry fetcher.

Detects ecosystem and queries package registries for metadata.
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

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
    "docker": ["Dockerfile"],
}


def _detect_ecosystems(repo_path: Path | None) -> list[str]:
    """Detect which ecosystems the project belongs to based on manifest files."""
    if not repo_path or not repo_path.exists():
        return []

    ecosystems = []
    for ecosystem, patterns in _ECOSYSTEM_PATTERNS.items():
        for pattern in patterns:
            if "*" in pattern:
                if list(repo_path.glob(pattern)):
                    ecosystems.append(ecosystem)
                    break
            elif (repo_path / pattern).exists():
                ecosystems.append(ecosystem)
                break

    return ecosystems


# pylint: disable=too-many-branches
# mccabe: MC0001
def _extract_package_name(repo_path: Path, ecosystem: str) -> str | None:
    """Extract package name from manifest files."""
    try:
        if ecosystem == "pypi":
            pyproject = repo_path / "pyproject.toml"
            if pyproject.exists():
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
                return data.get("project", {}).get("name")

        if ecosystem == "npm":
            pkg_json = repo_path / "package.json"
            if pkg_json.exists():
                with open(pkg_json, encoding="utf-8") as f:
                    data = json.load(f)
                name = data.get("name", "")
                # Handle scoped packages (@org/name)
                return name

        if ecosystem == "crates.io":
            cargo = repo_path / "Cargo.toml"
            if cargo.exists():
                with open(cargo, "rb") as f:
                    data = tomllib.load(f)
                return data.get("package", {}).get("name")

        if ecosystem == "go":
            go_mod = repo_path / "go.mod"
            if go_mod.exists():
                with open(go_mod, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("module "):
                            return line.split()[1].strip()

        if ecosystem == "rubygems":
            # Look for .gemspec files
            for gemspec in repo_path.glob("*.gemspec"):
                with open(gemspec, encoding="utf-8") as f:
                    content = f.read()
                # Simple regex to extract name
                match = re.search(r'\.name\s*=\s*["\']([^"\']+)["\']', content)
                if match:
                    return match.group(1)

    except Exception:
        pass

    return None


async def _fetch_pypi(package_name: str, cache: Cache) -> RegistryInfo:
    """Fetch package info from PyPI."""
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
                cache.set_json(cache_key, data, ttl_seconds=7 * 86400)  # 7 days
                return _parse_pypi_response(data, info)
    except Exception:
        pass

    return info


def _parse_pypi_response(data: dict[str, Any], info: RegistryInfo) -> RegistryInfo:
    """Parse PyPI API response."""
    info.exists = True

    # Latest version
    info.latest_version = data.get("info", {}).get("version")

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

    return info


async def _fetch_npm(package_name: str, cache: Cache) -> RegistryInfo:
    """Fetch package info from npm."""
    info = RegistryInfo(ecosystem="npm", package_name=package_name)

    cache_key = f"npm:{package_name}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return _parse_npm_response(cached, info)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # npm registry URL
            url = f"https://registry.npmjs.org/{package_name}"
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

    return info


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


async def fetch_registry_info(
    repo: Repository, local_path: str | None = None, cache: Cache | None = None
) -> list[RegistryInfo]:
    """Detect and fetch package registry information.

    Args:
        repo: Repository model (used to infer package name if local_path not provided)
        local_path: Path to local clone (for manifest file detection)
        cache: Cache instance for HTTP responses

    Returns:
        List of RegistryInfo for each detected ecosystem
    """
    if cache is None:
        cache = Cache(str(Path(user_cache_dir("gh-score")) / "cache"))

    repo_path = Path(local_path) if local_path else None
    ecosystems = _detect_ecosystems(repo_path)

    if not ecosystems:
        return []

    results: list[RegistryInfo] = []

    for ecosystem in ecosystems:
        package_name = _extract_package_name(repo_path, ecosystem) if repo_path else None
        if not package_name:
            # Try to infer from repo name
            package_name = repo.url.repo

        if ecosystem == "pypi":
            info = await _fetch_pypi(package_name, cache)
            results.append(info)
        elif ecosystem == "npm":
            info = await _fetch_npm(package_name, cache)
            results.append(info)
        elif ecosystem == "crates.io":
            info = await _fetch_crates(package_name, cache)
            results.append(info)
        # Other registries can be added here

    return results
