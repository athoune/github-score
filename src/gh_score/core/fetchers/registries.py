"""Package registry fetcher.

Detects ecosystem and queries package registries for metadata.
Supports: PyPI, npm, crates.io, Go (pkg.go.dev), RubyGems, Maven Central, Docker Hub.
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
    "docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
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


def _extract_python_package_name(repo_path: Path) -> str | None:
    """Extract Python package name from pyproject.toml or setup.cfg."""
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("name")

    setup_cfg = repo_path / "setup.cfg"
    if setup_cfg.exists():
        with open(setup_cfg, encoding="utf-8") as f:
            content = f.read()
        match = re.search(r"name\s*=\s*(.+)", content)
        if match:
            return match.group(1).strip()
    return None


def _extract_npm_package_name(repo_path: Path) -> str | None:
    """Extract npm package name from package.json."""
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        with open(pkg_json, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("name", "")
    return None


def _extract_crate_name(repo_path: Path) -> str | None:
    """Extract Rust crate name from Cargo.toml."""
    cargo = repo_path / "Cargo.toml"
    if cargo.exists():
        with open(cargo, "rb") as f:
            data = tomllib.load(f)
        return data.get("package", {}).get("name")
    return None


def _extract_go_module_path(repo_path: Path) -> str | None:
    """Extract Go module path from go.mod."""
    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        with open(go_mod, encoding="utf-8") as f:
            for line in f:
                if line.startswith("module "):
                    return line.split()[1].strip()
    return None


def _extract_gem_name(repo_path: Path) -> str | None:
    """Extract Ruby gem name from .gemspec files."""
    for gemspec in repo_path.glob("*.gemspec"):
        with open(gemspec, encoding="utf-8") as f:
            content = f.read()
        match = re.search(r'\.name\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    return None


def _extract_maven_coordinates(repo_path: Path) -> str | None:
    """Extract Maven groupId:artifactId from pom.xml."""
    pom = repo_path / "pom.xml"
    if pom.exists():
        with open(pom, encoding="utf-8") as f:
            content = f.read()
        group_match = re.search(r"<groupId>([^<]+)</groupId>", content)
        artifact_match = re.search(r"<artifactId>([^<]+)</artifactId>", content)
        if group_match and artifact_match:
            return f"{group_match.group(1)}:{artifact_match.group(1)}"
    return None


def _extract_docker_image_name(repo_path: Path) -> str | None:
    """Extract Docker image name from docker-compose files."""
    for compose_name in ("docker-compose.yml", "docker-compose.yaml"):
        compose = repo_path / compose_name
        if compose.exists():
            with open(compose, encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"image:\s*(\S+)", content)
            if match:
                return match.group(1)
    return None


def _extract_package_name(repo_path: Path, ecosystem: str) -> str | None:
    """Extract package name from manifest files for a given ecosystem."""
    extractors = {
        "pypi": _extract_python_package_name,
        "npm": _extract_npm_package_name,
        "crates.io": _extract_crate_name,
        "go": _extract_go_module_path,
        "rubygems": _extract_gem_name,
        "maven": _extract_maven_coordinates,
        "docker": _extract_docker_image_name,
    }
    try:
        extractor = extractors.get(ecosystem)
        if extractor:
            return extractor(repo_path)
    except Exception:
        pass
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
    """Fetch the number of packages importing this module (popularity proxy)."""
    cache_key = f"go-imported-by:{module_path}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached.get("count")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://pkg.go.dev/v1beta/imported-by/{module_path}"
            )
            if resp.status_code == 200:
                data = resp.json()
                count = len(data.get("importedBy", []))
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

        # Timestamp
        timestamp = doc.get("timestamp")
        if timestamp:
            try:
                info.latest_date = datetime.fromtimestamp(timestamp / 1000)
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
# Main entry point
# ---------------------------------------------------------------------------

# pylint: disable=too-many-branches
# mccabe: MC0001
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
            # No manifest file or no name inside it — skip this ecosystem
            # rather than guessing from the repository name (risk of homonyms).
            continue

        if ecosystem == "pypi":
            info = await _fetch_pypi(package_name, cache)
            # Fetch download stats
            info.downloads = await _fetch_pypi_downloads(package_name, cache)
            results.append(info)
        elif ecosystem == "npm":
            info = await _fetch_npm(package_name, cache)
            # Fetch download stats
            info.recent_downloads = await _fetch_npm_downloads(package_name, cache)
            results.append(info)
        elif ecosystem == "crates.io":
            info = await _fetch_crates(package_name, cache)
            results.append(info)
        elif ecosystem == "go":
            info = await _fetch_go(package_name, cache)
            results.append(info)
        elif ecosystem == "rubygems":
            info = await _fetch_rubygems(package_name, cache)
            results.append(info)
        elif ecosystem == "maven":
            info = await _fetch_maven(package_name, cache)
            results.append(info)
        elif ecosystem == "docker":
            info = await _fetch_docker(package_name, cache)
            results.append(info)

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
