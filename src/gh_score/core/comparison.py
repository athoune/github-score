"""Multi-repository comparison.

Compares several projects and assesses whether the comparison is
credible: the projects must address the same subject, and libraries
must be consumable from a common language (primary language, registry
publications, binding directories). The assessment is deterministic; the
optional LLM can only lift an ``unknown`` subject verdict (see
``llm/provider.py``).

Pair verdicts never block: a non-credible comparison is still produced,
with the flagged pairs clearly explained.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from enum import Enum

from gh_score.core.models import AnalysisResult, RepoUrl
from gh_score.i18n import t


class ProjectKind(Enum):
    LIBRARY = "library"
    APPLICATION = "application"
    UNKNOWN = "unknown"


class SubjectVerdict(Enum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class ComparisonVerdict(Enum):
    OK = "ok"
    WARNING = "warning"


@dataclass
class PairComparison:
    """Comparability assessment for one pair of projects."""

    url_a: RepoUrl
    url_b: RepoUrl
    kinds: tuple[ProjectKind, ProjectKind]
    subject: SubjectVerdict
    language_compatible: bool | None  # None when the language rule does not apply
    kind_mismatch: bool  # library vs application
    verdict: ComparisonVerdict
    reasons: list[str] = field(default_factory=list)  # localized explanation lines
    notes: list[str] = field(default_factory=list)  # informational, e.g. unknown kind


@dataclass
class ComparisonResult:
    """Aggregate comparison of N projects."""

    projects: list[AnalysisResult]
    pairs: list[PairComparison] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # localized flagged-pair summary


# ---------------------------------------------------------------------------
# Project classification (library / application)
# ---------------------------------------------------------------------------

# Registry ecosystems that publish consumable code (Docker excluded: a
# container image is an application, not a library).
_CODE_REGISTRY_ECOSYSTEMS = frozenset({"pypi", "npm", "crates.io", "rubygems", "maven", "go"})

_DOCKER_ROOT_FILES = frozenset({"dockerfile", "docker-compose.yml", "docker-compose.yaml"})

_MANIFEST_NAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    "cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
})

# Description keywords. "web" is deliberately absent: a "web framework"
# is a library, so "web" must not count as an application signal.
_LIBRARY_KEYWORDS = frozenset({
    "library", "framework", "sdk", "toolkit", "binding", "wrapper",
    "client", "package", "api",
})

_APPLICATION_KEYWORDS = frozenset({
    "application", "app", "cli", "command-line", "tool", "server",
    "daemon", "bot", "website", "webapp", "service", "utility",
})


def _has_any_keyword(text: str, keywords: frozenset[str]) -> bool:
    """Word-boundary keyword search (so "app" does not match "happy")."""
    return any(re.search(rf"\b{re.escape(kw)}\b", text) for kw in keywords)


def classify_project(result: AnalysisResult) -> ProjectKind:
    """Classify a project as library / application / unknown.

    First matching rule wins, see SPECS §8.2. ``unknown`` is relaxed to
    ``application`` by the callers of this function for the language rules.
    """
    # 1. Published on a code registry → library (it is consumable as code).
    if any(
        reg.exists and reg.ecosystem in _CODE_REGISTRY_ECOSYSTEMS
        for reg in result.registries
    ):
        return ProjectKind.LIBRARY

    root = set(result.root_files)
    has_docker = any(name in _DOCKER_ROOT_FILES for name in root)
    has_manifest = any(
        name in _MANIFEST_NAMES or name.endswith(".gemspec") for name in root
    )

    # 2. Docker without any manifest → application.
    if has_docker and not has_manifest:
        return ProjectKind.APPLICATION

    # 3. Description keywords.
    desc = (result.meta.description or "").lower()
    lib_words = _has_any_keyword(desc, _LIBRARY_KEYWORDS)
    app_words = _has_any_keyword(desc, _APPLICATION_KEYWORDS)
    if lib_words and not app_words:
        return ProjectKind.LIBRARY
    if app_words and not lib_words:
        return ProjectKind.APPLICATION

    # 4. A manifest exists → library.
    if has_manifest:
        return ProjectKind.LIBRARY

    # 5. No evidence → unknown.
    return ProjectKind.UNKNOWN


# ---------------------------------------------------------------------------
# Consumer languages
# ---------------------------------------------------------------------------

_ECOSYSTEM_LANGUAGES = {
    "pypi": "python",
    "npm": "javascript",
    "crates.io": "rust",
    "rubygems": "ruby",
    "maven": "java",
    "go": "go",
}

# JavaScript and TypeScript are the same language for comparison purposes;
# aliases cover common binding-directory names too.
_LANGUAGE_NORMALIZATION = {
    "typescript": "javascript",
    "ts": "javascript",
    "nodejs": "javascript",
    "node": "javascript",
    "js": "javascript",
    "py": "python",
    "rs": "rust",
    "rb": "ruby",
}

# Explicit binding directories at the root, e.g. "bindings/python".
_BINDING_DIR_RE = re.compile(r"^bindings[/\\]([a-z0-9_.+-]+)$")


def normalize_language(lang: str) -> str:
    """Normalize a language name for comparison (JS ≡ TS, lowercased)."""
    normalized = lang.strip().lower()
    return _LANGUAGE_NORMALIZATION.get(normalized, normalized)


def _binding_languages(root_files: list[str]) -> frozenset[str]:
    """Languages exposed through ``bindings/<lang>`` root directories."""
    langs = set()
    for name in root_files:
        m = _BINDING_DIR_RE.match(name.lower())
        if m:
            langs.add(normalize_language(m.group(1)))
    return frozenset(langs)


def consumer_languages(result: AnalysisResult) -> frozenset[str]:
    """Languages a project can be consumed from: its primary language
    plus the languages implied by registry publications and binding
    directories."""
    langs = set()
    if result.languages and result.languages.primary:
        langs.add(normalize_language(result.languages.primary))
    for reg in result.registries:
        if reg.exists and reg.ecosystem in _ECOSYSTEM_LANGUAGES:
            langs.add(_ECOSYSTEM_LANGUAGES[reg.ecosystem])
    langs.update(_binding_languages(result.root_files))
    return frozenset(langs)


# ---------------------------------------------------------------------------
# Subject comparability
# ---------------------------------------------------------------------------

_SUBJECT_MIN_TOKEN_LEN = 3

# Language names carry no subject information: a "python" topic is shared
# by every Python project whatever its purpose. Used both to exclude
# language topics and to filter description tokens.
_GENERIC_LANGUAGE_NAMES = frozenset({
    "python", "javascript", "typescript", "rust", "go", "golang", "java",
    "ruby", "c", "c++", "cpp", "php", "swift", "kotlin", "scala",
    "csharp", "elixir", "haskell", "clojure", "dart", "objective",
    "perl", "lua", "shell", "bash", "gcc",
})

# GitHub topics that are too generic to discriminate subjects.
_GENERIC_TOPICS = _GENERIC_LANGUAGE_NAMES | frozenset({
    "library", "libraries", "framework", "frameworks",
    "hacktoberfest", "awesome",
})

# Function words, generic project words and language names: they carry no
# subject information and would produce false positives ("a python tool"
# vs "a python framework" share "python" and "tool").
_SUBJECT_GENERIC_TOKENS = frozenset({
    # function words
    "the", "a", "an", "and", "or", "for", "with", "of", "to", "in", "on",
    "is", "are", "was", "were", "that", "this", "these", "those", "it",
    "as", "at", "by", "from", "into", "your", "you", "not", "be", "been",
    # generic project words (and common plurals)
    "project", "projects", "library", "libraries", "tool", "tools",
    "package", "packages", "application", "applications", "app", "apps",
    "framework", "frameworks", "sdk", "api", "apis", "module", "modules",
    "simple", "easy", "fast", "quick", "high", "performance", "efficient",
    "support", "supports", "supporting", "use", "using", "used", "user",
    "users", "built", "written", "based", "make", "making", "provide",
    "provides", "allows", "allowing", "designed", "code", "source",
    "open", "free", "small", "lightweight", "modern", "full",
}) | _GENERIC_LANGUAGE_NAMES


def _description_tokens(description: str | None) -> frozenset[str]:
    """Meaningful tokens of a description (stopwords/generics removed)."""
    if not description:
        return frozenset()
    tokens = set()
    for word in re.findall(r"[a-z0-9]+", description.lower()):
        if len(word) >= _SUBJECT_MIN_TOKEN_LEN and word not in _SUBJECT_GENERIC_TOKENS:
            tokens.add(word)
    return frozenset(tokens)


def _meaningful_topics(result: AnalysisResult) -> frozenset[str]:
    """Topics that carry subject information (language/generic tags out)."""
    return frozenset(
        x.strip().lower()
        for x in result.meta.topics
        if x.strip() and x.strip().lower() not in _GENERIC_TOPICS
    )


def _assess_subject(a: AnalysisResult, b: AnalysisResult) -> tuple[SubjectVerdict, str]:
    """Subject verdict with a localized explanation.

    Non-generic topics when both projects have some, then description
    keywords, then ``unknown`` (see SPECS §8.4). Disjoint non-generic
    topics are not decisive on their own: the descriptions get a second
    opinion before the pair is judged incompatible.
    """
    topics_a = _meaningful_topics(a)
    topics_b = _meaningful_topics(b)
    if topics_a and topics_b:
        shared = topics_a & topics_b
        if shared:
            return SubjectVerdict.COMPATIBLE, t(
                "cmp_subject_topic", topics=", ".join(sorted(shared))
            )
        # Both tagged, no overlap: fall through to the description rule.

    tokens_a = _description_tokens(a.meta.description)
    tokens_b = _description_tokens(b.meta.description)
    if tokens_a and tokens_b:
        shared = tokens_a & tokens_b
        if shared:
            return SubjectVerdict.COMPATIBLE, t(
                "cmp_subject_desc", tokens=", ".join(sorted(shared))
            )
        return SubjectVerdict.INCOMPATIBLE, t("cmp_subject_desc_disjoint")

    return SubjectVerdict.UNKNOWN, t("cmp_subject_unknown")


# ---------------------------------------------------------------------------
# Pair assessment
# ---------------------------------------------------------------------------


def assess_pair(a: AnalysisResult, b: AnalysisResult) -> PairComparison:
    """Assess the comparability of two projects (SPECS §8.5)."""
    kind_a, kind_b = classify_project(a), classify_project(b)

    subject, subject_reason = _assess_subject(a, b)
    reasons = [subject_reason]
    notes = []
    for result, kind in ((a, kind_a), (b, kind_b)):
        if kind == ProjectKind.UNKNOWN:
            notes.append(t("cmp_kind_unknown", repo=str(result.url)))

    kind_mismatch = (
        (kind_a == ProjectKind.LIBRARY and kind_b == ProjectKind.APPLICATION)
        or (kind_a == ProjectKind.APPLICATION and kind_b == ProjectKind.LIBRARY)
    )
    if kind_mismatch:
        reasons.append(t("cmp_kind_mismatch"))

    # The language rule applies to libraries only (unknown is relaxed to
    # application, so no language constraint). An empty consumer set means
    # the language could not be determined — never judge it incompatible.
    language_compatible: bool | None = None
    if kind_a == ProjectKind.LIBRARY and kind_b == ProjectKind.LIBRARY:
        langs_a = consumer_languages(a)
        langs_b = consumer_languages(b)
        if langs_a and langs_b:
            shared = langs_a & langs_b
            language_compatible = bool(shared)
            if language_compatible:
                reasons.append(
                    t("cmp_language_compatible", langs=", ".join(sorted(shared)))
                )
            else:
                reasons.append(
                    t(
                        "cmp_language_incompatible",
                        langs_a=", ".join(sorted(langs_a)),
                        langs_b=", ".join(sorted(langs_b)),
                    )
                )

    verdict = ComparisonVerdict.OK
    if (
        subject in (SubjectVerdict.INCOMPATIBLE, SubjectVerdict.UNKNOWN)
        or kind_mismatch
        or language_compatible is False
    ):
        verdict = ComparisonVerdict.WARNING

    return PairComparison(
        url_a=a.url,
        url_b=b.url,
        kinds=(kind_a, kind_b),
        subject=subject,
        language_compatible=language_compatible,
        kind_mismatch=kind_mismatch,
        verdict=verdict,
        reasons=reasons,
        notes=notes,
    )


def compare_results(results: list[AnalysisResult]) -> ComparisonResult:
    """Assess every pair of the given analysis results."""
    pairs = [assess_pair(a, b) for a, b in itertools.combinations(results, 2)]
    warnings = [
        t("cmp_warning", a=str(p.url_a), b=str(p.url_b))
        for p in pairs
        if p.verdict == ComparisonVerdict.WARNING
    ]
    return ComparisonResult(projects=list(results), pairs=pairs, warnings=warnings)
