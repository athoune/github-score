"""Tests for CLI helpers extracted from main.py."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from gh_score.cli.main import _prepare_config, _resolve_target, _validate_url, cli


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
            mock_cfg.return_value = MagicMock()
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
            mock_cfg.return_value = MagicMock()
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
