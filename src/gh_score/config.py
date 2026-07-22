"""Configuration loading for gh-score.

Priority: CLI flags > environment variables > config file > defaults.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_cache_dir


@dataclass
class GitHubConfig:
    token: str = ""


@dataclass
class CacheConfig:
    dir: str = ""
    ttl_hours: int = 24


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""


@dataclass
class DashboardConfig:
    colors: bool = True
    thresholds: dict[str, Any] = field(default_factory=lambda: {
        "stale_days": 180,
        "maintenance_commits_per_month": 2,
        "abandoned_months": 6,
        "bus_factor_warning": 2,
        "bus_factor_critical": 1,
    })


@dataclass
class Config:
    github: GitHubConfig = field(default_factory=GitHubConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    @classmethod
    def load(cls, config_path: str | None = None) -> Config:
        """Load configuration from file, env vars, and defaults."""
        cfg = cls()

        # Load from config file
        if config_path is None:
            config_path = os.environ.get(
                "GH_SCORE_CONFIG",
                str(Path(user_config_dir("gh-score")) / "config.toml"),
            )
        path = Path(config_path)
        if path.exists():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            cfg._apply_toml(data)

        # Override with environment variables
        cfg._apply_env()

        # Set default cache dir if not configured
        if not cfg.cache.dir:
            cfg.cache.dir = str(Path(user_cache_dir("gh-score")))

        return cfg

    def _apply_toml(self, data: dict[str, Any]) -> None:
        if "github" in data:
            if "token" in data["github"]:
                self.github.token = data["github"]["token"]
        if "cache" in data:
            if "dir" in data["cache"]:
                self.cache.dir = data["cache"]["dir"]
            if "ttl_hours" in data["cache"]:
                self.cache.ttl_hours = int(data["cache"]["ttl_hours"])
        if "llm" in data:
            llm = data["llm"]
            if "enabled" in llm:
                self.llm.enabled = bool(llm["enabled"])
            if "provider" in llm:
                self.llm.provider = llm["provider"]
            if "model" in llm:
                self.llm.model = llm["model"]
            if "base_url" in llm:
                self.llm.base_url = llm["base_url"]
            if "api_key" in llm:
                self.llm.api_key = llm["api_key"]
        if "dashboard" in data:
            dash = data["dashboard"]
            if "colors" in dash:
                self.dashboard.colors = bool(dash["colors"])
            if "thresholds" in dash:
                self.dashboard.thresholds.update(dash["thresholds"])

    def _apply_env(self) -> None:
        if token := os.environ.get("GITHUB_TOKEN"):
            self.github.token = token
        if val := os.environ.get("GH_SCORE_CACHE_DIR"):
            self.cache.dir = val
        if val := os.environ.get("GH_SCORE_CACHE_TTL_HOURS"):
            self.cache.ttl_hours = int(val)
        if val := os.environ.get("GH_SCORE_LLM_ENABLED"):
            self.llm.enabled = val.lower() in ("1", "true", "yes")
        if val := os.environ.get("GH_SCORE_LLM_PROVIDER"):
            self.llm.provider = val
        if val := os.environ.get("GH_SCORE_LLM_MODEL"):
            self.llm.model = val
        if val := os.environ.get("GH_SCORE_LLM_BASE_URL"):
            self.llm.base_url = val
        if val := os.environ.get("GH_SCORE_LLM_API_KEY"):
            self.llm.api_key = val
