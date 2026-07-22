"""Local git repository fetcher.

Extracts data from a local git clone using gitpython.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from git import Repo

from gh_score.core.models import (
    Commit,
    CommunityFiles,
    Contributor,
    ContributorStats,
    LanguageBreakdown,
    RepoUrl,
    Repository,
    RepositoryMeta,
)


def _parse_github_remote(remote_url: str) -> RepoUrl | None:
    """Parse a GitHub URL from a git remote (supports SSH and HTTPS)."""
    # SSH: git@github.com:owner/repo.git
    ssh_match = re.match(r"git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$", remote_url)
    if ssh_match:
        return RepoUrl(owner=ssh_match.group("owner"), repo=ssh_match.group("repo"))

    # HTTPS: https://github.com/owner/repo.git
    https_match = re.match(
        r"https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$",
        remote_url,
    )
    if https_match:
        return RepoUrl(owner=https_match.group("owner"), repo=https_match.group("repo"))

    return None


def _detect_ecosystem(repo_path: Path) -> str | None:
    """Detect the primary ecosystem from manifest files."""
    checks = [
        ("pyproject.toml", "python"),
        ("setup.py", "python"),
        ("setup.cfg", "python"),
        ("package.json", "javascript"),
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("pom.xml", "java"),
        ("build.gradle", "java"),
        ("build.gradle.kts", "java"),
        ("Gemfile", "ruby"),
        ("*.gemspec", "ruby"),
        ("Dockerfile", "docker"),
    ]

    for filename, ecosystem in checks:
        if "*" in filename:
            if list(repo_path.glob(filename)):
                return ecosystem
        elif (repo_path / filename).exists():
            return ecosystem

    return None


def _extract_package_name(repo_path: Path, ecosystem: str) -> str | None:
    """Extract package name from manifest files."""
    try:
        if ecosystem == "python":
            pyproject = repo_path / "pyproject.toml"
            if pyproject.exists():
                import tomllib
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
                return data.get("project", {}).get("name")

        elif ecosystem == "javascript":
            import json
            pkg_json = repo_path / "package.json"
            if pkg_json.exists():
                with open(pkg_json) as f:
                    data = json.load(f)
                return data.get("name")

        elif ecosystem == "rust":
            cargo = repo_path / "Cargo.toml"
            if cargo.exists():
                import tomllib
                with open(cargo, "rb") as f:
                    data = tomllib.load(f)
                return data.get("package", {}).get("name")

        elif ecosystem == "go":
            go_mod = repo_path / "go.mod"
            if go_mod.exists():
                with open(go_mod) as f:
                    for line in f:
                        if line.startswith("module "):
                            return line.split()[1].strip()
    except Exception:
        pass

    return None


def fetch_local_repo(path: str) -> Repository:
    """Fetch data from a local git clone.

    Args:
        path: Path to the local git repository

    Returns:
        Repository model populated with local data

    Raises:
        ValueError: If path is not a valid git repository
    """
    repo_path = Path(path).resolve()

    try:
        git_repo = Repo(repo_path)
    except Exception as e:
        raise ValueError(f"Not a valid git repository: {path}") from e

    # Detect GitHub remote URL
    remote_url = None
    for remote in git_repo.remotes:
        url = remote.url
        parsed = _parse_github_remote(url)
        if parsed:
            remote_url = parsed
            break

    if not remote_url:
        raise ValueError(
            f"No GitHub remote found in {path}. "
            "Use 'gh-score <repo-url>' for remote analysis."
        )

    repo = Repository(url=remote_url)

    # Extract commit history
    commits: list[Commit] = []
    contributor_commits: dict[str, int] = {}

    try:
        head = git_repo.head.commit
        for commit in git_repo.iter_commits(head, max_count=10000):
            # Use author name as login (email is protected/masked by GitHub)
            author_login = commit.author.name
            author_date = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)

            commits.append(Commit(
                sha=commit.hexsha,
                author_login=author_login,
                author_email=commit.author.email,
                author_date=author_date,
                message=commit.message,
            ))

            # Track contributor commits by name (not email)
            key = commit.author.name
            contributor_commits[key] = contributor_commits.get(key, 0) + 1
    except Exception:
        pass

    repo.commits = commits

    # Build contributor stats
    contributors = [
        Contributor(login=login, commits=count)
        for login, count in sorted(
            contributor_commits.items(), key=lambda x: x[1], reverse=True
        )
    ]
    repo.contributors = ContributorStats(
        contributors=contributors,
        total_commit_count=len(commits),
    )

    # Detect ecosystem
    ecosystem = _detect_ecosystem(repo_path)
    package_name = _extract_package_name(repo_path, ecosystem) if ecosystem else None

    # Check community files
    community = CommunityFiles()
    file_checks = {
        "README.md": "has_readme",
        "readme.md": "has_readme",
        "LICENSE": "has_license",
        "LICENSE.md": "has_license",
        "license": "has_license",
        "CONTRIBUTING.md": "has_contributing",
        "CODE_OF_CONDUCT.md": "has_code_of_conduct",
        "SECURITY.md": "has_security",
        "GOVERNANCE.md": "has_governance",
        "CHANGELOG.md": "has_changelog",
        "NEWS.md": "has_changelog",
    }

    for filename, attr in file_checks.items():
        if (repo_path / filename).exists():
            setattr(community, attr, True)

    # Check FUNDING.yml
    funding_paths = [
        repo_path / ".github" / "FUNDING.yml",
        repo_path / "FUNDING.yml",
    ]
    for funding_path in funding_paths:
        if funding_path.exists():
            try:
                with open(funding_path) as f:
                    content = f.read()
                # Simple parse
                community.funding = _parse_funding_local(content)
                community.has_funding = True  # type: ignore[attr-defined]
            except Exception:
                pass
            break

    repo.community = community

    # Read README if available
    for readme_name in ["README.md", "readme.md", "README.rst"]:
        readme_path = repo_path / readme_name
        if readme_path.exists():
            try:
                with open(readme_path) as f:
                    repo.readme_content = f.read()
            except Exception:
                pass
            break

    # Read GOVERNANCE if available
    for gov_name in ["GOVERNANCE.md", "governance.md"]:
        gov_path = repo_path / gov_name
        if gov_path.exists():
            try:
                with open(gov_path) as f:
                    repo.governance_content = f.read()
            except Exception:
                pass
            break

    # Read SECURITY if available
    for sec_name in ["SECURITY.md", "security.md"]:
        sec_path = repo_path / sec_name
        if sec_path.exists():
            try:
                with open(sec_path) as f:
                    repo.security_content = f.read()
            except Exception:
                pass
            break

    # Basic meta from git
    try:
        head_commit = git_repo.head.commit
        repo.meta = RepositoryMeta(
            name=remote_url.repo,
            full_name=f"{remote_url.owner}/{remote_url.repo}",
            owner=remote_url.owner,
            default_branch=git_repo.active_branch.name if not git_repo.head.is_detached else "main",
            pushed_at=datetime.fromtimestamp(head_commit.committed_date, tz=timezone.utc),
        )
    except Exception:
        repo.meta = RepositoryMeta(
            name=remote_url.repo,
            full_name=f"{remote_url.owner}/{remote_url.repo}",
            owner=remote_url.owner,
        )

    # Language breakdown from file extensions (rough approximation)
    languages = _estimate_languages(repo_path)
    repo.languages = languages

    return repo


def _estimate_languages(repo_path: Path) -> LanguageBreakdown:
    """Estimate language breakdown from file extensions in the repo."""
    ext_map = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".jsx": "JavaScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".kt": "Kotlin",
        ".rb": "Ruby",
        ".php": "PHP",
        ".c": "C",
        ".cpp": "C++",
        ".h": "C",
        ".hpp": "C++",
        ".cs": "C#",
        ".swift": "Swift",
        ".scala": "Scala",
        ".sh": "Shell",
        ".md": "Markdown",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".toml": "TOML",
    }

    counts: dict[str, int] = {}
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

    try:
        for file_path in repo_path.rglob("*"):
            if not file_path.is_file():
                continue
            # Skip hidden and common non-source directories
            parts = file_path.relative_to(repo_path).parts
            if any(p in skip_dirs or p.startswith(".") for p in parts):
                continue

            ext = file_path.suffix.lower()
            lang = ext_map.get(ext)
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    except Exception:
        pass

    # Convert to bytes-like values (just use file counts as proxy)
    languages = {lang: count * 1000 for lang, count in counts.items()}
    return LanguageBreakdown(languages=languages)


def _parse_funding_local(content: str) -> dict[str, list[str]]:
    """Parse FUNDING.yml content."""
    result: dict[str, list[str]] = {}
    current_key: str | None = None

    for line in content.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        if ":" in line_stripped and not line_stripped.startswith((" ", "-", "\t")):
            key, _, value = line_stripped.partition(":")
            key = key.strip().lower()
            value = value.strip()

            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                result[key] = [i.strip().strip("'\"") for i in items if i.strip()]
                current_key = None
            elif value:
                result[key] = [value.strip("'\"")]
                current_key = None
            else:
                current_key = key
                result.setdefault(key, [])
        elif current_key and line_stripped.startswith("-"):
            item = line_stripped[1:].strip().strip("'\"")
            if item:
                result.setdefault(current_key, []).append(item)

    return result
