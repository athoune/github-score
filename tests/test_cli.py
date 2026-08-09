"""Tests for CLI helpers extracted from main.py."""

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from gh_score.cli.main import (
    _prepare_config,
    _render_comparison_json,
    _render_comparison_markdown,
    _resolve_target,
    _validate_url,
    cli,
)
from gh_score.core.comparison import ComparisonResult, compare_results
from gh_score.core.models import (
    AnalysisResult,
    ContributorsIndicator,
    LanguagesIndicator,
    LicenseIndicator,
    MaintenanceIndicator,
    MaintenanceState,
    Recommendation,
    RecommendationLevel,
    ReleaseHealthIndicator,
    RepoUrl,
    RepositoryMeta,
    SustainabilityIndicator,
)


def _mock_config() -> MagicMock:
    """Config mock with the LLM disabled: a bare MagicMock would make
    ``config.llm.enabled`` truthy and trigger a real LLM call."""
    config = MagicMock()
    config.llm.enabled = False
    return config


def _result_with_warnings(*warnings: str) -> AnalysisResult:
    """Minimal AnalysisResult exercising the renderers."""
    return AnalysisResult(
        url=RepoUrl("owner", "repo"),
        meta=RepositoryMeta(description="A demo"),
        release_health=ReleaseHealthIndicator(),
        license=LicenseIndicator(),
        contributors=ContributorsIndicator(),
        maintenance=MaintenanceIndicator(),
        languages=LanguagesIndicator(),
        sustainability=SustainabilityIndicator(),
        recommendation=Recommendation(),
        warnings=list(warnings),
    )


class TestResolveTarget:
    """Tests for _resolve_target."""

    def test_with_url(self):
        resolved, is_local = _resolve_target("https://github.com/owner/repo")
        assert resolved == "https://github.com/owner/repo"
        assert is_local is False

    def test_with_local_git_path(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        resolved, is_local = _resolve_target(str(tmp_path))
        assert resolved == str(tmp_path)
        assert is_local is True

    def test_with_local_non_git_path(self, tmp_path):
        resolved, is_local = _resolve_target(str(tmp_path))
        assert resolved == str(tmp_path)
        assert is_local is False

    def test_none_with_git_cwd(self, monkeypatch, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        resolved, is_local = _resolve_target(None)
        assert resolved == str(tmp_path)
        assert is_local is True

    def test_none_without_git_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        resolved, is_local = _resolve_target(None)
        assert resolved is None
        assert is_local is False


class TestValidateUrl:
    """Tests for _validate_url."""

    def test_local_mode_skips_validation(self):
        console = Console(file=open("/dev/null", "w"))
        # Should not raise even with garbage input
        _validate_url("not-a-url", local=True, console=console)

    def test_existing_path_skips_validation(self, tmp_path):
        console = Console(file=open("/dev/null", "w"))
        _validate_url(str(tmp_path), local=False, console=console)

    def test_valid_url(self):
        console = Console(file=open("/dev/null", "w"))
        _validate_url("https://github.com/owner/repo", local=False, console=console)

    def test_invalid_url_exits(self, capsys):
        console = Console()
        with pytest.raises(SystemExit) as exc_info:
            _validate_url("https://gitlab.com/owner/repo", local=False, console=console)
        assert exc_info.value.code == 1


class TestPrepareConfig:
    """Tests for _prepare_config."""

    def test_loads_config(self):
        mock_config = MagicMock()
        mock_config.cache.dir = "/tmp/cache"
        mock_config.llm.enabled = True

        with patch("gh_score.cli.main.Config.load", return_value=mock_config):
            config = _prepare_config(
                config_path="/fake/config.toml",
                refresh=False,
                no_llm=False,
                console=Console(file=open("/dev/null", "w")),
            )
        assert config is mock_config
        assert config.llm.enabled is True

    def test_refresh_clears_cache(self):
        mock_config = MagicMock()
        mock_config.cache.dir = "/tmp/cache"
        mock_config.llm.enabled = True

        mock_cache = MagicMock()

        with patch("gh_score.cli.main.Config.load", return_value=mock_config):
            with patch("gh_score.cli.main.Cache", return_value=mock_cache):
                config = _prepare_config(
                    config_path=None,
                    refresh=True,
                    no_llm=False,
                    console=Console(file=open("/dev/null", "w")),
                )
        mock_cache.clear.assert_called_once()
        assert config is mock_config

    def test_no_llm_disables_llm(self):
        mock_config = MagicMock()
        mock_config.cache.dir = "/tmp/cache"
        mock_config.llm.enabled = True

        with patch("gh_score.cli.main.Config.load", return_value=mock_config):
            config = _prepare_config(
                config_path=None,
                refresh=False,
                no_llm=True,
                console=Console(file=open("/dev/null", "w")),
            )
        assert config.llm.enabled is False


class TestDefaultGroupForwardsArgs:
    """Regression: DefaultGroup must forward positional args (URL) to the
    default command.  Previously the URL was silently dropped, causing the
    CWD to be analysed instead of the requested remote repository.

    Also verify that real subcommands (``config``, ``report``) are NOT
    mistaken for URLs.
    """

    def test_url_forwarded_to_analyze(self):
        runner = CliRunner()
        with (
            patch("gh_score.cli.main.analyze_repo") as mock_analyze,
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            mock_analyze.return_value = MagicMock(url="https://github.com/o/r")
            runner.invoke(cli, ["https://github.com/o/r"])

        assert mock_analyze.called, "analyze_repo was never called"
        url_arg = mock_analyze.call_args[0][0]
        assert url_arg == "https://github.com/o/r"

    def test_no_args_uses_cwd(self):
        """Without arguments and inside a git repo, the CWD should be used."""
        runner = CliRunner()
        with (
            patch("gh_score.cli.main.analyze_repo") as mock_analyze,
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists", return_value=True),
        ):
            mock_cfg.return_value = _mock_config()
            mock_analyze.return_value = MagicMock(url="https://github.com/o/r")
            mock_cwd.return_value = MagicMock(**{"__str__": lambda s: "/some/path"})
            runner.invoke(cli, [])

        # Without a URL, the CWD must be analyzed
        assert mock_analyze.called, "analyze_repo should analyze the CWD"
        url_arg = mock_analyze.call_args[0][0]
        assert url_arg == "/some/path"

    @pytest.mark.parametrize(
        ("lang", "expected_title"),
        [
            ("en_US.UTF-8", "Current Configuration"),
            ("fr_FR.UTF-8", "Configuration actuelle"),
        ],
    )
    def test_config_command_not_mistaken_for_url(self, monkeypatch, lang, expected_title):
        """``gh-score config`` must run the config command (localized output),
        NOT treat 'config' as a URL for the analyze command.

        Both supported languages are exercised, with the locale pinned so the
        test is independent of the machine's $LANG.
        """
        monkeypatch.setenv("LANG", lang)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)

        runner = CliRunner()
        with (
            patch("gh_score.cli.main.analyze_repo") as mock_analyze,
            patch("gh_score.cli.main.Config") as mock_config_cls,
        ):
            mock_config_cls.load.return_value = MagicMock(
                github=MagicMock(token="tok"),
                cache=MagicMock(dir="/tmp", ttl_hours=24),
                llm=MagicMock(
                    enabled=False, provider="ollama",
                    model="m", base_url="http://x",
                ),
            )
            result = runner.invoke(cli, ["config"])

        assert not mock_analyze.called, (
            "analyze_repo should NOT be called for 'gh-score config'"
        )
        assert expected_title in result.output


class TestComparisonCli:
    """Two or more URLs route to comparison mode (SPECS §8.1)."""

    @staticmethod
    def _mock_config():
        """Config mock with the LLM disabled (its MagicMock default would
        be truthy and trigger a real LLM call in the comparison path)."""
        config = MagicMock()
        config.llm.enabled = False
        return config

    @staticmethod
    def _result(name: str, topics: list[str]) -> AnalysisResult:
        return AnalysisResult(
            url=RepoUrl("owner", name),
            meta=RepositoryMeta(full_name=f"owner/{name}", stars=100, topics=topics),
            release_health=ReleaseHealthIndicator(),
            license=LicenseIndicator(),
            contributors=ContributorsIndicator(),
            maintenance=MaintenanceIndicator(state=MaintenanceState.ACTIVE),
            languages=LanguagesIndicator(primary="Python"),
            sustainability=SustainabilityIndicator(),
            recommendation=Recommendation(level=RecommendationLevel.GREEN),
        )

    def test_two_urls_render_comparison(self):
        runner = CliRunner()
        with (
            patch(
                "gh_score.cli.main.analyze_repo_async",
                side_effect=[
                    self._result("fastapi", ["http"]),
                    self._result("flask", ["http"]),
                ],
            ) as mock_async,
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, ["https://github.com/a/fastapi", "https://github.com/b/flask"])

        assert result.exit_code == 0
        assert mock_async.await_count == 2
        assert "Project comparison" in result.output
        assert "owner/fastapi" in result.output
        assert "owner/flask" in result.output

    def test_two_urls_json_format(self):
        runner = CliRunner()
        with (
            patch(
                "gh_score.cli.main.analyze_repo_async",
                side_effect=[
                    self._result("fastapi", ["http"]),
                    self._result("flask", ["http"]),
                ],
            ),
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, [
                "https://github.com/a/fastapi", "https://github.com/b/flask",
                "--format", "json",
            ])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert len(payload["projects"]) == 2
        assert len(payload["pairs"]) == 1
        assert payload["pairs"][0]["verdict"] == "ok"

    def test_two_urls_markdown_format(self):
        runner = CliRunner()
        with (
            patch(
                "gh_score.cli.main.analyze_repo_async",
                side_effect=[
                    self._result("fastapi", ["http"]),
                    self._result("flask", ["http"]),
                ],
            ),
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, [
                "https://github.com/a/fastapi", "https://github.com/b/flask",
                "--format", "markdown",
            ])

        assert result.exit_code == 0
        assert "# GitHub Health Comparison" in result.output
        assert "## Comparability" in result.output

    def test_single_url_keeps_single_analysis(self):
        """A single URL must NOT enter comparison mode."""
        runner = CliRunner()
        with (
            patch("gh_score.cli.main.analyze_repo") as mock_analyze,
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            mock_analyze.return_value = self._result("fastapi", ["http"])
            result = runner.invoke(cli, ["https://github.com/a/fastapi"])

        assert mock_analyze.called
        assert "Project comparison" not in result.output

    def test_report_with_two_urls_compares(self):
        runner = CliRunner()
        with (
            patch(
                "gh_score.cli.main.analyze_repo_async",
                side_effect=[
                    self._result("fastapi", ["http"]),
                    self._result("flask", ["http"]),
                ],
            ),
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, [
                "report", "https://github.com/a/fastapi", "https://github.com/b/flask",
            ])

        assert result.exit_code == 0
        assert "Project comparison" in result.output

    def test_llm_refinement_called_when_enabled(self):
        runner = CliRunner()
        config = MagicMock()
        config.llm.enabled = True
        with (
            patch(
                "gh_score.cli.main.analyze_repo_async",
                side_effect=[
                    self._result("fastapi", ["http"]),
                    self._result("flask", ["http"]),
                ],
            ),
            patch("gh_score.cli.main._prepare_config", return_value=config),
            patch(
                "gh_score.cli.main.refine_comparison_subjects",
                new=AsyncMock(),
            ) as mock_refine,
        ):
            result = runner.invoke(cli, [
                "https://github.com/a/fastapi", "https://github.com/b/flask",
            ])

        assert result.exit_code == 0
        mock_refine.assert_awaited_once()

    def test_invalid_url_in_comparison_exits(self):
        runner = CliRunner()
        with patch("gh_score.cli.main._prepare_config") as mock_cfg:
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, [
                "https://github.com/a/fastapi", "https://gitlab.com/b/flask",
            ])

        assert result.exit_code == 1


class TestWarningsStderr:
    """File-based formats emit warnings on stderr."""

    @pytest.mark.parametrize("fmt", ["markdown", "json"])
    def test_warnings_on_stderr(self, fmt):
        runner = CliRunner()
        analysis = _result_with_warnings("No GitHub token set")

        with (
            patch("gh_score.cli.main.analyze_repo", return_value=analysis),
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, ["https://github.com/o/r", "--format", fmt])

        assert "No GitHub token set" in result.stderr

    def test_no_warnings_no_stderr_noise(self):
        runner = CliRunner()
        analysis = _result_with_warnings()

        with (
            patch("gh_score.cli.main.analyze_repo", return_value=analysis),
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, ["https://github.com/o/r", "--format", "markdown"])

        assert "warning" not in result.stderr


class TestHelpEnvVars:
    """--help must document the environment variables."""

    def test_group_help_lists_env_vars(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "GITHUB_TOKEN" in result.output
        assert "GH_SCORE_LLM_BASE_URL" in result.output
        assert "GH_SCORE_LLM_API_KEY" in result.output

    def test_analyze_help_lists_env_vars(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "GITHUB_TOKEN" in result.output


class TestMarkdownReport:
    """The markdown report includes the website availability section."""

    def test_website_section_present(self):
        runner = CliRunner()
        analysis = _result_with_warnings()

        with (
            patch("gh_score.cli.main.analyze_repo", return_value=analysis),
            patch("gh_score.cli.main._prepare_config") as mock_cfg,
        ):
            mock_cfg.return_value = _mock_config()
            result = runner.invoke(cli, ["https://github.com/o/r", "--format", "markdown"])

        assert result.exit_code == 0
        assert "## Website" in result.output


class TestComparisonRenderers:
    """JSON / Markdown comparison renderers (wired by the CLI wiring
    commit; tested directly here)."""

    @staticmethod
    def _comparison() -> ComparisonResult:
        a = AnalysisResult(
            url=RepoUrl("owner", "fastapi"),
            meta=RepositoryMeta(full_name="owner/fastapi", stars=76000, topics=["http"]),
            release_health=ReleaseHealthIndicator(),
            license=LicenseIndicator(spdx_id="MIT"),
            contributors=ContributorsIndicator(),
            maintenance=MaintenanceIndicator(
                state=MaintenanceState.ACTIVE, last_commit_days_ago=2
            ),
            languages=LanguagesIndicator(primary="Python"),
            sustainability=SustainabilityIndicator(),
            recommendation=Recommendation(level=RecommendationLevel.GREEN),
        )
        b = AnalysisResult(
            url=RepoUrl("owner", "asyncpg"),
            meta=RepositoryMeta(full_name="owner/asyncpg", stars=9000, topics=["database"]),
            release_health=ReleaseHealthIndicator(),
            license=LicenseIndicator(spdx_id="MIT"),
            contributors=ContributorsIndicator(),
            maintenance=MaintenanceIndicator(
                state=MaintenanceState.ACTIVE, last_commit_days_ago=0
            ),
            languages=LanguagesIndicator(primary="Python"),
            sustainability=SustainabilityIndicator(),
            recommendation=Recommendation(level=RecommendationLevel.ORANGE),
        )
        a.warnings = ["No GitHub token set"]
        return compare_results([a, b])

    @pytest.mark.parametrize("fmt", ["markdown", "json"])
    def test_warnings_on_stderr(self, fmt, capsys):
        buf = io.StringIO()
        console = Console(file=buf)
        if fmt == "markdown":
            _render_comparison_markdown(self._comparison(), console)
        else:
            _render_comparison_json(self._comparison(), console)
        captured = capsys.readouterr()
        assert "No GitHub token set" in captured.err

    def test_markdown_has_comparability_and_table(self):
        buf = io.StringIO()
        console = Console(file=buf)
        _render_comparison_markdown(self._comparison(), console)
        output = buf.getvalue()
        assert "# GitHub Health Comparison" in output
        assert "## Comparability" in output
        assert "## Comparison" in output
        # Flagged pair with its reason (subjects not verifiable: disjoint
        # topics, no descriptions)
        assert "owner/fastapi vs owner/asyncpg" in output
        assert "subjects cannot be verified" in output
        # Table with per-project rows and verdict glyphs
        assert "| owner/fastapi | 76,000 |" in output
        assert "| owner/asyncpg | 9,000 |" in output
        assert "🟠" in output
        # Full per-project reports are embedded
        assert "GitHub Health Report: " in output

    def test_json_structure(self):
        buf = io.StringIO()
        console = Console(file=buf)
        _render_comparison_json(self._comparison(), console)
        payload = json.loads(buf.getvalue())
        assert len(payload["projects"]) == 2
        assert len(payload["pairs"]) == 1
        pair = payload["pairs"][0]
        assert pair["url_a"] == "https://github.com/owner/fastapi"
        assert pair["subject"] == "unknown"
        assert pair["language_compatible"] is None
        assert pair["verdict"] == "warning"
        assert len(payload["warnings"]) == 1
        # Projects keep the single-analysis JSON shape
        assert payload["projects"][0]["meta"]["stars"] == 76000
