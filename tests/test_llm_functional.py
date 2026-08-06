"""Functional tests exercising a real LLM provider.

Skipped unless GH_SCORE_LLM_ENABLED is truthy. The connection settings
come from config or env: GH_SCORE_LLM_ENABLED, GH_SCORE_LLM_BASE_URL,
GH_SCORE_LLM_MODEL, GH_SCORE_LLM_API_KEY.
"""

import os

import pytest

from gh_score.config import LLMConfig
from gh_score.core.models import QualitativeSignals, Repository, RepoUrl
from gh_score.llm.provider import analyze_qualitative_with_llm


def _llm_configured() -> bool:
    return os.environ.get("GH_SCORE_LLM_ENABLED", "").lower() in ("1", "true", "yes")


requires_llm = pytest.mark.skipif(
    not _llm_configured(),
    reason="Functional LLM tests need GH_SCORE_LLM_ENABLED=true and a reachable provider",
)


def _llm_config() -> LLMConfig:
    return LLMConfig(
        enabled=True,
        base_url=os.environ.get(
            "GH_SCORE_LLM_BASE_URL", "http://localhost:11434/v1"
        ),
        model=os.environ.get("GH_SCORE_LLM_MODEL", "llama3.2"),
        api_key=os.environ.get("GH_SCORE_LLM_API_KEY", ""),
    )


@requires_llm
@pytest.mark.asyncio
async def test_real_llm_extracts_qualitative_signals():
    repo = Repository(url=RepoUrl("owner", "repo"))
    repo.readme_content = (
        "# demo\n\n"
        "## Roadmap\nWe plan a v2 with async support.\n\n"
        "## Security\nVulnerabilities are handled via GitHub private advisories.\n\n"
        "This project is actively maintained.\n"
    )

    signals = await analyze_qualitative_with_llm(repo, _llm_config())

    assert isinstance(signals, QualitativeSignals)
    # At least one of the four signals should be extracted from this text.
    assert signals.any is True
    assert signals.text_maintenance_state in ("active", "maintenance", None)
    # The LLM must not invent fields we removed from the prompt schema.
    assert not hasattr(signals, "sponsors")


@requires_llm
@pytest.mark.asyncio
async def test_real_llm_no_text_returns_empty():
    repo = Repository(url=RepoUrl("owner", "repo"))  # no readme/governance/security

    signals = await analyze_qualitative_with_llm(repo, _llm_config())

    assert signals == QualitativeSignals()
    assert signals.any is False
