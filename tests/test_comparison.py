"""Tests for the multi-repo comparison engine (core/comparison.py)."""

from __future__ import annotations

import pytest

from gh_score.core.comparison import (
    ComparisonVerdict,
    ProjectKind,
    SubjectVerdict,
    apply_subject_refinement,
    assess_pair,
    classify_project,
    compare_results,
    consumer_languages,
    normalize_language,
    ranked_projects,
    similarity_score,
)
from gh_score.core.models import (
    AnalysisResult,
    ContributorsIndicator,
    LanguagesIndicator,
    LicenseIndicator,
    MaintenanceIndicator,
    Recommendation,
    RecommendationLevel,
    RegistryInfo,
    ReleaseHealthIndicator,
    RepoUrl,
    RepositoryMeta,
    SustainabilityIndicator,
)
from gh_score.i18n import t


def _result(
    name: str = "repo",
    description: str | None = None,
    topics: list[str] | None = None,
    primary: str | None = None,
    registries: list[RegistryInfo] | None = None,
    root_files: list[str] | None = None,
) -> AnalysisResult:
    """Minimal AnalysisResult exercising the comparison engine."""
    return AnalysisResult(
        url=RepoUrl("owner", name),
        meta=RepositoryMeta(description=description, topics=topics or []),
        release_health=ReleaseHealthIndicator(),
        license=LicenseIndicator(),
        contributors=ContributorsIndicator(),
        maintenance=MaintenanceIndicator(),
        languages=LanguagesIndicator(primary=primary),
        sustainability=SustainabilityIndicator(),
        recommendation=Recommendation(),
        registries=registries or [],
        root_files=root_files or [],
    )


def _pinned_en(monkeypatch) -> None:
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)


class TestClassifyProject:
    def test_registry_publication_is_library(self):
        result = _result(registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        assert classify_project(result) == ProjectKind.LIBRARY

    def test_unpublished_registry_is_not_library(self):
        result = _result(registries=[RegistryInfo(ecosystem="pypi", exists=False)])
        assert classify_project(result) != ProjectKind.LIBRARY

    def test_dockerfile_without_manifest_is_application(self):
        result = _result(root_files=["dockerfile", "readme.md"])
        assert classify_project(result) == ProjectKind.APPLICATION

    def test_dockerfile_with_manifest_is_library(self):
        result = _result(root_files=["dockerfile", "pyproject.toml"])
        assert classify_project(result) == ProjectKind.LIBRARY

    def test_description_library_words(self):
        result = _result(description="A fast HTTP framework")
        assert classify_project(result) == ProjectKind.LIBRARY

    def test_description_application_words(self):
        result = _result(description="A command-line tool for sysadmins")
        assert classify_project(result) == ProjectKind.APPLICATION

    def test_description_app_word_does_not_match_substring(self):
        # "happy" must not trigger the "app" keyword.
        result = _result(description="Make everyone happy")
        assert classify_project(result) == ProjectKind.UNKNOWN

    def test_manifest_is_library(self):
        result = _result(root_files=["cargo.toml"])
        assert classify_project(result) == ProjectKind.LIBRARY

    def test_gemspec_is_library(self):
        result = _result(root_files=["mylib.gemspec"])
        assert classify_project(result) == ProjectKind.LIBRARY

    def test_no_evidence_is_unknown(self):
        result = _result(description=None, root_files=["readme.md"])
        assert classify_project(result) == ProjectKind.UNKNOWN


class TestConsumerLanguages:
    def test_primary_language_only(self):
        result = _result(primary="Python")
        assert consumer_languages(result) == {"python"}

    def test_registry_extends_consumer_languages(self):
        result = _result(
            primary="Rust",
            registries=[
                RegistryInfo(ecosystem="crates.io", exists=True),
                RegistryInfo(ecosystem="pypi", exists=True),
            ],
        )
        # A Rust project published on PyPI exposes Python bindings.
        assert consumer_languages(result) == {"rust", "python"}

    def test_typescript_normalized_to_javascript(self):
        assert consumer_languages(_result(primary="TypeScript")) == {"javascript"}

    def test_binding_directory(self):
        result = _result(primary="Rust", root_files=["bindings/python", "cargo.toml"])
        assert consumer_languages(result) == {"rust", "python"}

    def test_empty(self):
        assert consumer_languages(_result()) == set()

    def test_normalize_language(self):
        assert normalize_language("TypeScript") == "javascript"
        assert normalize_language("Python") == "python"
        assert normalize_language("UnknownLang") == "unknownlang"


class TestAssessPair:
    @pytest.fixture(autouse=True)
    def _english(self, monkeypatch):
        _pinned_en(monkeypatch)

    def test_compatible_libraries_shared_topic(self):
        a = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        b = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        pair = assess_pair(a, b)
        assert pair.verdict == ComparisonVerdict.OK
        assert pair.subject == SubjectVerdict.COMPATIBLE
        assert pair.language_compatible is True
        assert t("cmp_subject_topic", lang="en", topics="http") in pair.reasons

    def test_disjoint_topics_without_descriptions_unknown(self):
        # Disjoint non-generic topics are not decisive on their own: the
        # descriptions get a second opinion, and without any the subject
        # is unknown (still flagged as a warning).
        a = _result(topics=["http"])
        b = _result(topics=["database"])
        pair = assess_pair(a, b)
        assert pair.verdict == ComparisonVerdict.WARNING
        assert pair.subject == SubjectVerdict.UNKNOWN

    def test_disjoint_topics_and_descriptions_warn(self):
        a = _result(topics=["http"], description="HTTP framework")
        b = _result(topics=["database"], description="PostgreSQL driver")
        pair = assess_pair(a, b)
        assert pair.subject == SubjectVerdict.INCOMPATIBLE
        assert pair.verdict == ComparisonVerdict.WARNING

    def test_language_topic_is_not_a_subject_signal(self):
        # Both projects share the "python" topic only: the descriptions
        # decide. "database" is shared → compatible.
        a = _result(topics=["python"], description="PostgreSQL database driver")
        b = _result(topics=["python"], description="The database toolkit")
        pair = assess_pair(a, b)
        assert pair.subject == SubjectVerdict.COMPATIBLE
        assert t("cmp_subject_desc", lang="en", tokens="database") in pair.reasons

    def test_description_keywords_match(self):
        a = _result(description="An HTTP client library for Python")
        b = _result(description="HTTP client written in Rust")
        pair = assess_pair(a, b)
        assert pair.subject == SubjectVerdict.COMPATIBLE
        prefix = t("cmp_subject_desc", lang="en", tokens="")
        assert any(r.startswith(prefix) and "http" in r for r in pair.reasons)

    def test_library_without_detected_language_not_judged_incompatible(self):
        # Descriptions classify both as libraries, but no primary language
        # and no registry → the language cannot be judged, not incompatible.
        a = _result(description="An HTTP client library for Python")
        b = _result(description="HTTP client written in Rust")
        pair = assess_pair(a, b)
        assert pair.language_compatible is None
        assert pair.verdict == ComparisonVerdict.OK

    def test_disjoint_descriptions_warn(self):
        a = _result(description="Async PostgreSQL database driver")
        b = _result(description="HTTP framework for Python")
        pair = assess_pair(a, b)
        assert pair.subject == SubjectVerdict.INCOMPATIBLE
        assert pair.verdict == ComparisonVerdict.WARNING

    def test_unknown_subject_warns(self):
        a = _result(description=None)
        b = _result(description=None)
        pair = assess_pair(a, b)
        assert pair.subject == SubjectVerdict.UNKNOWN
        assert pair.verdict == ComparisonVerdict.WARNING

    def test_libraries_in_different_languages_warn(self):
        a = _result(primary="Python", registries=[RegistryInfo(ecosystem="pypi", exists=True)], topics=["http"])
        b = _result(primary="Rust", registries=[RegistryInfo(ecosystem="crates.io", exists=True)], topics=["http"])
        pair = assess_pair(a, b)
        assert pair.language_compatible is False
        assert pair.verdict == ComparisonVerdict.WARNING
        assert t("cmp_language_incompatible", lang="en", langs_a="python", langs_b="rust") in pair.reasons

    def test_binding_project_compatible_with_native_library(self):
        # Rust project with Python bindings vs a pure Python library.
        a = _result(
            primary="Rust",
            registries=[
                RegistryInfo(ecosystem="crates.io", exists=True),
                RegistryInfo(ecosystem="pypi", exists=True),
            ],
            topics=["http"],
        )
        b = _result(primary="Python", registries=[RegistryInfo(ecosystem="pypi", exists=True)], topics=["http"])
        pair = assess_pair(a, b)
        assert pair.language_compatible is True
        assert pair.verdict == ComparisonVerdict.OK

    def test_library_vs_application_mismatch(self):
        a = _result(registries=[RegistryInfo(ecosystem="pypi", exists=True)], topics=["http"])
        b = _result(root_files=["dockerfile"], topics=["http"])
        pair = assess_pair(a, b)
        assert pair.kind_mismatch is True
        assert pair.verdict == ComparisonVerdict.WARNING

    def test_applications_have_no_language_rule(self):
        a = _result(description="A command-line tool for git", topics=["git"])
        b = _result(description="A server application for teams", topics=["git"])
        pair = assess_pair(a, b)
        assert pair.kinds == (ProjectKind.APPLICATION, ProjectKind.APPLICATION)
        assert pair.language_compatible is None
        assert pair.verdict == ComparisonVerdict.OK

    def test_unknown_kind_noted(self):
        a = _result(description=None, root_files=["readme.md"])
        b = _result(description=None, root_files=["readme.md"])
        pair = assess_pair(a, b)
        assert any("unknown project kind" in note for note in pair.notes)
        # Unknown kind relaxes to application: no language constraint.
        assert pair.language_compatible is None

    def test_typescript_and_javascript_compatible(self):
        a = _result(primary="TypeScript", registries=[RegistryInfo(ecosystem="npm", exists=True)], topics=["http"])
        b = _result(primary="JavaScript", registries=[RegistryInfo(ecosystem="npm", exists=True)], topics=["http"])
        pair = assess_pair(a, b)
        assert pair.language_compatible is True
        assert pair.verdict == ComparisonVerdict.OK

    def test_cross_signal_topic_vs_description(self):
        # "web" as a topic of one project and "web" in the other's
        # description is still a shared subject (FastAPI vs Flask pattern).
        a = _result(topics=["web", "framework"], description="High performance API framework")
        b = _result(topics=["flask", "wsgi"], description="Micro framework for building web applications")
        pair = assess_pair(a, b)
        assert pair.subject == SubjectVerdict.COMPATIBLE
        assert t("cmp_subject_signals", lang="en", signals="web") in pair.reasons


class TestSimilarityScore:
    """Informational 0.0–1.0 lexical closeness, never part of the verdict."""

    def test_compatible_libraries_blend_language(self):
        a = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        b = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        # Shared topic "http" only: topic Jaccard 1.0, language Jaccard 1.0.
        assert similarity_score(a, b, SubjectVerdict.COMPATIBLE) == 1.0

    def test_different_languages_penalize_the_score(self):
        a = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        b = _result(primary="Rust", topics=["http"], registries=[RegistryInfo(ecosystem="crates.io", exists=True)])
        # Topic Jaccard 1.0 (0.7) + language Jaccard 0.0 (0.3).
        assert similarity_score(a, b, SubjectVerdict.COMPATIBLE) == 0.7

    def test_incompatible_is_zero(self):
        a = _result(description="PostgreSQL database driver")
        b = _result(description="HTTP framework for Python")
        assert similarity_score(a, b, SubjectVerdict.INCOMPATIBLE) == 0.0

    def test_unknown_subject_is_none(self):
        a = _result(topics=["http"])
        b = _result(topics=["database"])
        assert similarity_score(a, b, SubjectVerdict.UNKNOWN) is None

    def test_no_signals_is_none(self):
        # Defensive: compatible verdicts normally imply shared signals.
        a = _result()
        b = _result()
        assert similarity_score(a, b, SubjectVerdict.COMPATIBLE) is None

    def test_assess_pair_carries_similarity(self):
        a = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        b = _result(primary="Python", topics=["http"], registries=[RegistryInfo(ecosystem="pypi", exists=True)])
        pair = assess_pair(a, b)
        assert pair.similarity == 1.0

    def test_incompatible_pair_similarity_zero(self):
        a = _result(description="PostgreSQL database driver")
        b = _result(description="HTTP framework for Python")
        pair = assess_pair(a, b)
        assert pair.similarity == 0.0


class TestCompareResults:
    @pytest.fixture(autouse=True)
    def _english(self, monkeypatch):
        _pinned_en(monkeypatch)

    def test_pairs_for_three_projects(self):
        a = _result(name="a", topics=["http"])
        b = _result(name="b", topics=["http"])
        c = _result(name="c", topics=["database"])
        comparison = compare_results([a, b, c])
        assert len(comparison.pairs) == 3
        assert comparison.projects == [a, b, c]

    def test_warnings_only_for_flagged_pairs(self):
        a = _result(name="a", topics=["http"])
        b = _result(name="b", topics=["http"])
        c = _result(name="c", topics=["database"])
        comparison = compare_results([a, b, c])
        # (a,c) and (b,c) are flagged, (a,b) is not.
        assert len(comparison.warnings) == 2
        assert comparison.pairs[2].verdict == ComparisonVerdict.WARNING

    def test_no_warnings_when_all_ok(self):
        a = _result(name="a", topics=["http"])
        b = _result(name="b", topics=["http"])
        assert compare_results([a, b]).warnings == []


class TestRankedProjects:
    """Decision-table order: verdict, then downloads, then bus factor."""

    def _ranked(self, results) -> list[str]:
        return [r.url.repo for r in ranked_projects(compare_results(results))]

    def test_verdict_first_then_downloads(self):
        green_low = _result(
            name="green-low",
            registries=[RegistryInfo(ecosystem="pypi", exists=True, downloads=100)],
        )
        green_high = _result(
            name="green-high",
            registries=[RegistryInfo(ecosystem="pypi", exists=True, downloads=9000)],
        )
        red = _result(
            name="red",
            registries=[RegistryInfo(ecosystem="pypi", exists=True, downloads=999999)],
        )
        green_high.recommendation.level = RecommendationLevel.GREEN
        green_low.recommendation.level = RecommendationLevel.GREEN
        red.recommendation.level = RecommendationLevel.RED
        # The red project has the most downloads but the worst verdict.
        assert self._ranked([red, green_low, green_high]) == [
            "green-high",
            "green-low",
            "red",
        ]

    def test_bus_factor_breaks_download_ties(self):
        a = _result(
            name="a",
            registries=[RegistryInfo(ecosystem="pypi", exists=True, downloads=100)],
        )
        b = _result(
            name="b",
            registries=[RegistryInfo(ecosystem="pypi", exists=True, downloads=100)],
        )
        a.recommendation.level = RecommendationLevel.GREEN
        b.recommendation.level = RecommendationLevel.GREEN
        a.contributors = ContributorsIndicator(bus_factor=2)
        b.contributors = ContributorsIndicator(bus_factor=5)
        assert self._ranked([a, b]) == ["b", "a"]


class TestApplySubjectRefinement:
    """LLM subject judgments only lift deterministic 'unknown' verdicts."""

    @pytest.fixture(autouse=True)
    def _english(self, monkeypatch):
        _pinned_en(monkeypatch)

    @staticmethod
    def _unknown_pair():
        # Disjoint non-generic topics, no descriptions → subject unknown.
        a = _result(name="a", topics=["http"])
        b = _result(name="b", topics=["database"])
        return compare_results([a, b]), a, b

    def test_lifts_unknown_to_compatible(self):
        comparison, a, b = self._unknown_pair()
        pair = comparison.pairs[0]
        assert pair.subject == SubjectVerdict.UNKNOWN
        assert pair.verdict == ComparisonVerdict.WARNING

        apply_subject_refinement(
            comparison, {frozenset({str(a.url), str(b.url)}): True}
        )

        assert pair.subject == SubjectVerdict.COMPATIBLE
        assert pair.verdict == ComparisonVerdict.OK
        assert comparison.warnings == []
        assert t("cmp_subject_llm_compatible", lang="en") in pair.reasons

    def test_lifts_unknown_to_incompatible(self):
        comparison, a, b = self._unknown_pair()
        pair = comparison.pairs[0]

        apply_subject_refinement(
            comparison, {frozenset({str(a.url), str(b.url)}): False}
        )

        assert pair.subject == SubjectVerdict.INCOMPATIBLE
        assert pair.verdict == ComparisonVerdict.WARNING
        assert len(comparison.warnings) == 1

    def test_deterministic_verdict_never_overridden(self):
        a = _result(name="a", topics=["http"])
        b = _result(name="b", topics=["http"])
        comparison = compare_results([a, b])
        pair = comparison.pairs[0]
        assert pair.subject == SubjectVerdict.COMPATIBLE

        # The LLM disagrees: it must be ignored.
        apply_subject_refinement(
            comparison, {frozenset({str(a.url), str(b.url)}): False}
        )

        assert pair.subject == SubjectVerdict.COMPATIBLE
        assert pair.verdict == ComparisonVerdict.OK

    def test_missing_llm_verdict_leaves_pair_untouched(self):
        comparison, _, _ = self._unknown_pair()
        pair = comparison.pairs[0]

        apply_subject_refinement(comparison, {})

        assert pair.subject == SubjectVerdict.UNKNOWN
        assert pair.verdict == ComparisonVerdict.WARNING

    def test_lift_unlocks_similarity(self):
        # A lifted subject unlocks the similarity score that was None.
        comparison, a, b = self._unknown_pair()
        pair = comparison.pairs[0]
        assert pair.similarity is None

        apply_subject_refinement(
            comparison, {frozenset({str(a.url), str(b.url)}): True}
        )

        assert pair.similarity is not None
        assert pair.subject == SubjectVerdict.COMPATIBLE
