"""Tests for the qualitative (LLM) analyzer."""

from gh_score.core.analyzers.qualitative import analyze_qualitative
from gh_score.core.models import (
    QualitativeSignals,
    Repository,
    RepoUrl,
    Status,
)


def _repo(signals: QualitativeSignals | None = None) -> Repository:
    repo = Repository(url=RepoUrl("owner", "repo"))
    repo.llm_signals = signals or QualitativeSignals()
    return repo


class TestAnalyzeQualitative:
    def test_no_signals_not_available(self):
        ind = analyze_qualitative(_repo())
        assert ind.available is False
        assert ind.status == Status.UNKNOWN

    def test_signals_mapped_and_available(self):
        ind = analyze_qualitative(_repo(QualitativeSignals(
            roadmap="v2",
            text_maintenance_state="active",
        )))
        assert ind.available is True
        assert ind.status == Status.HEALTHY
        assert ind.roadmap == "v2"
        assert ind.text_maintenance_state == "active"

    def test_interpretation_mentions_signals(self):
        ind = analyze_qualitative(_repo(QualitativeSignals(
            roadmap="v2",
            commercial_support="paid tiers",
        )))
        assert ind.interpretation != ""
