"""Tests for configuration loading."""

import os
import tempfile
from pathlib import Path

from gh_score.config import Config
from gh_score.cli.main import _load_dotenv


_ENV_OVERRIDES = (
    "GITHUB_TOKEN",
    "GH_SCORE_CONFIG",
    "GH_SCORE_CACHE_DIR",
    "GH_SCORE_CACHE_TTL_HOURS",
    "GH_SCORE_LLM_ENABLED",
    "GH_SCORE_LLM_PROVIDER",
    "GH_SCORE_LLM_MODEL",
    "GH_SCORE_LLM_BASE_URL",
    "GH_SCORE_LLM_API_KEY",
    "LIBRARIES_IO_API_KEY",
)


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.github.token == ""
        assert config.cache.ttl_hours == 24
        assert config.llm.enabled is False
        assert config.llm.provider == "ollama"
        assert config.registries.libraries_io_api_key == ""

    def test_load_from_toml(self, monkeypatch):
        # Hermetic: GitHub Actions always sets GITHUB_TOKEN, and local shells
        # may set GH_SCORE_* vars — all of them override the TOML file.
        for var in _ENV_OVERRIDES:
            monkeypatch.delenv(var, raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text("""
[github]
token = "test_token"

[cache]
ttl_hours = 48

[llm]
enabled = true
provider = "openai"
model = "gpt-4"

[registries]
libraries_io_api_key = "lio_secret"
""")

            config = Config.load(str(config_path))
            assert config.github.token == "test_token"
            assert config.cache.ttl_hours == 48
            assert config.llm.enabled is True
            assert config.llm.provider == "openai"
            assert config.llm.model == "gpt-4"
            assert config.registries.libraries_io_api_key == "lio_secret"

    def test_registries_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                "[registries]\nlibraries_io_api_key = \"file_key\"\n"
            )

            os.environ["LIBRARIES_IO_API_KEY"] = "env_key"
            try:
                config = Config.load(str(config_path))
                # Env should override file
                assert config.registries.libraries_io_api_key == "env_key"
            finally:
                del os.environ["LIBRARIES_IO_API_KEY"]

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text("[github]\ntoken = \"file_token\"\n")

            # Set env var
            os.environ["GITHUB_TOKEN"] = "env_token"
            try:
                config = Config.load(str(config_path))
                # Env should override file
                assert config.github.token == "env_token"
            finally:
                del os.environ["GITHUB_TOKEN"]

    def test_missing_config_file(self, monkeypatch):
        # Should not raise, just use defaults.
        for var in _ENV_OVERRIDES:
            monkeypatch.delenv(var, raising=False)
        config = Config.load("/nonexistent/path/config.toml")
        assert config.github.token == ""


class TestLoadDotenv:
    """The CLI loads .env from the working directory so GH_SCORE_LLM_*
    and friends work without sourcing it."""

    def test_missing_file_is_noop(self, monkeypatch):
        monkeypatch.delenv("GH_SCORE_LLM_ENABLED", raising=False)
        _load_dotenv("/nonexistent/.env")
        assert "GH_SCORE_LLM_ENABLED" not in os.environ

    def test_parses_keys_and_export_prefix(self, tmp_path, monkeypatch):
        dotenv = tmp_path / ".env"
        dotenv.write_text(
            "# comment\n"
            "export GH_SCORE_LLM_ENABLED=true\n"
            'GH_SCORE_LLM_MODEL="Qwen3.5-2B-6bit"\n'
            "GITHUB_TOKEN=secret\n"
            "MALFORMED_LINE_NO_EQ\n"
        )
        for var in ("GH_SCORE_LLM_ENABLED", "GH_SCORE_LLM_MODEL", "GITHUB_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        _load_dotenv(str(dotenv))
        assert os.environ["GH_SCORE_LLM_ENABLED"] == "true"
        assert os.environ["GH_SCORE_LLM_MODEL"] == "Qwen3.5-2B-6bit"
        assert os.environ["GITHUB_TOKEN"] == "secret"

    def test_existing_env_var_wins(self, tmp_path, monkeypatch):
        dotenv = tmp_path / ".env"
        dotenv.write_text("GH_SCORE_LLM_MODEL=from-dotenv\n")
        monkeypatch.setenv("GH_SCORE_LLM_MODEL", "from-shell")
        _load_dotenv(str(dotenv))
        assert os.environ["GH_SCORE_LLM_MODEL"] == "from-shell"
