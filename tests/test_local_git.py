"""Tests for the local git fetcher.

Creates real throwaway git repositories with gitpython and verifies that
local analysis parses commits, contributors, community files, ecosystems
and languages correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from gh_score.core.fetchers.local_git import (
    _detect_ecosystem,
    _estimate_languages,
    _extract_package_name,
    _parse_funding_local,
    _parse_github_remote,
    fetch_local_repo,
)
from gh_score.core.models import RepoUrl


def _make_git_repo(
    tmp_path,
    remote_url: str = "https://github.com/owner/repo.git",
) -> tuple[Repo, Path]:
    """Initialize a git repo with an origin remote and a default identity."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = Repo.init(str(repo_path))
    repo.create_remote("origin", remote_url)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    return repo, repo_path


class TestParseGithubRemote:
    def test_ssh(self):
        url = _parse_github_remote("git@github.com:owner/repo.git")
        assert url == RepoUrl(owner="owner", repo="repo")

    def test_https(self):
        url = _parse_github_remote("https://github.com/owner/repo.git")
        assert url == RepoUrl(owner="owner", repo="repo")

    def test_https_no_suffix(self):
        url = _parse_github_remote("https://github.com/owner/repo")
        assert url == RepoUrl(owner="owner", repo="repo")

    def test_non_github(self):
        assert _parse_github_remote("git@gitlab.com:owner/repo.git") is None

    def test_invalid(self):
        assert _parse_github_remote("not-a-url") is None


class TestDetectEcosystem:
    def test_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        assert _detect_ecosystem(tmp_path) == "python"

    def test_javascript(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert _detect_ecosystem(tmp_path) == "javascript"

    def test_gemspec_glob(self, tmp_path):
        (tmp_path / "mylib.gemspec").write_text("spec.name = 'mylib'")
        assert _detect_ecosystem(tmp_path) == "ruby"

    def test_none(self, tmp_path):
        assert _detect_ecosystem(tmp_path) is None


class TestExtractPackageName:
    def test_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "mypkg"')
        assert _extract_package_name(tmp_path, "python") == "mypkg"

    def test_javascript(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "@scope/pkg"}')
        assert _extract_package_name(tmp_path, "javascript") == "@scope/pkg"

    def test_go_module(self, tmp_path):
        (tmp_path / "go.mod").write_text("module github.com/owner/repo\n")
        assert _extract_package_name(tmp_path, "go") == "github.com/owner/repo"

    def test_unknown_ecosystem(self, tmp_path):
        assert _extract_package_name(tmp_path, "docker") is None


class TestEstimateLanguages:
    def test_counts_by_extension(self, tmp_path):
        (tmp_path / "main.py").write_text("print(1)")
        (tmp_path / "app.js").write_text("console.log(1)")
        # Skipped: node_modules and hidden dirs
        (tmp_path / "node_modules" / "dep.js").mkdir(parents=True)
        (tmp_path / "node_modules" / "dep.js" / "x.js").write_text("x")
        (tmp_path / ".hidden" / "x.py").mkdir(parents=True)
        (tmp_path / ".hidden" / "x.py" / "y.py").write_text("y")

        lb = _estimate_languages(tmp_path)

        assert lb.languages == {"Python": 1000, "JavaScript": 1000}

    def test_empty(self, tmp_path):
        assert _estimate_languages(tmp_path).languages == {}


class TestParseFundingLocal:
    def test_inline_list(self):
        assert _parse_funding_local("github: [user1, user2]\n") == {
            "github": ["user1", "user2"],
        }

    def test_list_items(self):
        content = "open_collective:\n  - proj-a\n  - proj-b\n"
        assert _parse_funding_local(content) == {
            "open_collective": ["proj-a", "proj-b"],
        }


class TestFetchLocalRepo:
    def test_not_a_git_repo(self, tmp_path):
        with pytest.raises(ValueError, match="Not a valid git repository"):
            fetch_local_repo(str(tmp_path))

    def test_no_github_remote(self, tmp_path):
        repo, repo_path = _make_git_repo(tmp_path, remote_url="git@gitlab.com:o/r.git")
        (repo_path / "README.md").write_text("# x")
        repo.index.add(["README.md"])
        repo.index.commit("init")

        with pytest.raises(ValueError, match="No GitHub remote"):
            fetch_local_repo(str(repo_path))

    def test_parses_repository(self, tmp_path):
        repo, repo_path = _make_git_repo(tmp_path)
        (repo_path / "README.md").write_text("# Demo\n")
        (repo_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
        (repo_path / "CONTRIBUTING.md").write_text("how to contribute\n")
        (repo_path / ".github").mkdir()
        (repo_path / ".github" / "FUNDING.yml").write_text("github: [alice]\n")
        (repo_path / "main.py").write_text("print('hello')\n")
        (repo_path / "utils.py").write_text("def helper():\n    pass\n")
        (repo_path / "cli.py").write_text("import sys\n")
        repo.index.add(
            ["README.md", "pyproject.toml", "CONTRIBUTING.md",
             ".github/FUNDING.yml", "main.py", "utils.py", "cli.py"]
        )
        repo.index.commit("init")

        bob = Actor("Bob", "bob@example.com")
        repo.index.add(["README.md"])
        repo.index.commit("second", author=bob, committer=bob)

        result = fetch_local_repo(str(repo_path))

        assert result.url == RepoUrl(owner="owner", repo="repo")
        assert result.meta.name == "repo"
        assert result.meta.owner == "owner"
        assert result.meta.default_branch == "main"

        # Commits: one per author
        assert len(result.commits) == 2
        assert result.contributors.total_commit_count == 2
        by_login = {c.login: c for c in result.contributors.contributors}
        assert set(by_login) == {"Alice", "Bob"}

        # Community files and funding
        assert result.community.has_readme is True
        assert result.community.has_contributing is True
        assert result.community.has_funding is True  # type: ignore[attr-defined]
        assert result.community.funding == {"github": ["alice"]}

        # README content read from disk
        assert result.readme_content == "# Demo\n"

        # Ecosystem + languages (README.md -> Markdown, main.py -> Python)
        assert result.languages.primary == "Python"
