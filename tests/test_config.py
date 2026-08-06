"""Tests for configuration loading."""

import tempfile
import os
from pathlib import Path

from gh_score.config import Config


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
)


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.github.token == ""
        assert config.cache.ttl_hours == 24
        assert config.llm.enabled is False
        assert config.llm.provider == "ollama"

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
""")

            config = Config.load(str(config_path))
            assert config.github.token == "test_token"
            assert config.cache.ttl_hours == 48
            assert config.llm.enabled is True
            assert config.llm.provider == "openai"
            assert config.llm.model == "gpt-4"

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
