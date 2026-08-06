"""Tests for the LLM provider prompt and JSON parsing (no network)."""

from gh_score.core.models import QualitativeSignals
from gh_score.llm.provider import (
    _build_prompt,
    _parse_qualitative,
    _TEXT_MAINTENANCE_STATES,
)


class TestParseQualitative:
    def test_full_signals(self):
        raw = {
            "roadmap": "v2 with async support",
            "security_policy": "report via GitHub private advisory",
            "commercial_support": "paid support available",
            "text_maintenance_state": "active",
        }
        s = _parse_qualitative(raw)
        assert s == QualitativeSignals(
            roadmap="v2 with async support",
            security_policy="report via GitHub private advisory",
            commercial_support="paid support available",
            text_maintenance_state="active",
        )

    def test_null_and_missing_fields(self):
        s = _parse_qualitative({"roadmap": None})
        assert s.any is False

    def test_invalid_maintenance_state_rejected(self):
        s = _parse_qualitative({"text_maintenance_state": "kinda alive"})
        assert s.text_maintenance_state is None

    def test_state_values(self):
        assert _TEXT_MAINTENANCE_STATES == {"active", "maintenance", "abandoned", "unknown"}

    def test_blank_strings_become_none(self):
        s = _parse_qualitative({"roadmap": "  ", "commercial_support": ""})
        assert s.roadmap is None
        assert s.commercial_support is None


class TestPromptScope:
    """Regression: the LLM must not duplicate deterministic functions."""

    def test_prompt_excludes_sponsors_and_governance(self):
        prompt = _build_prompt("sustainability and governance")
        assert "sponsors" not in prompt
        assert "governance_model" not in prompt
        assert "roadmap" in prompt
        assert "text_maintenance_state" in prompt

    def test_prompt_contains_text_placeholder(self):
        prompt = _build_prompt("sustainability and governance")
        assert "{text}" in prompt
