"""Tests for CLI helpers extracted from main.py."""

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from gh_score.cli.main import _prepare_config, _resolve_target, _validate_url


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
