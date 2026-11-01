"""GitHub repository search: query building, candidate filtering, dedupe.

Searching answers a different question than analyzing: *which projects
exist* versus *is this project healthy*. The two axes are never mixed.

GitHub's repository search matches loosely. Measured on
``vega mcp language:python``: 17 results, of which 5 are about the Vega
visualization grammar; the rest are Amazon's Vega OS, Fallout New Vegas,
MAGIX VEGAS, the option Greek, and *Vegapunk*. Quoting does not help
(``"vega"`` returns the same 17), and ``-vega-os`` does not negate — it
returns exactly the two Vega OS repositories.

A lexical filter cannot fix this either, and it is worth writing down
why: the same prefix match that surfaces ``vegas`` also surfaces
``mcp-vegalite-server``, the single most relevant hit of that search.
Rejecting compound matches deletes the best answer with the noise. So
v1 keeps determinism where it is *knowable*:

1. the query itself (qualifiers GitHub understands, see ``SearchSpec``);
2. explicit exclusions (``--not``), matched on whole words so that
   rejecting ``vegas`` never rejects ``vegalite``;
3. declared mirrors and duplicates.

Every drop carries a reason so the caller can print an auditable funnel.
The health verdict never reorders relevance: analysis runs after the
funnel and only ever ranks the survivors among themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import get_close_matches

from gh_score.core.models import RepoUrl

# ---------------------------------------------------------------------------
# Language resolution
# ---------------------------------------------------------------------------

# Short forms users type mapped to canonical GitHub Linguist names. GitHub
# silently returns nothing for an unknown language, so every value is
# resolved (or refused) locally before it reaches the API.
_LANGUAGE_ALIASES: dict[str, str] = {
    "py": "Python",
    "python": "Python",
    "python3": "Python",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "node": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "rs": "Rust",
    "rust": "Rust",
    "go": "Go",
    "golang": "Go",
    "rb": "Ruby",
    "ruby": "Ruby",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "c++": "C++",
    "cxx": "C++",
    "cs": "C#",
    "csharp": "C#",
    "c#": "C#",
    "java": "Java",
    "kt": "Kotlin",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "php": "PHP",
    "sh": "Shell",
    "bash": "Shell",
    "shell": "Shell",
    "zsh": "Shell",
    "fish": "fish",
    "lua": "Lua",
    "zig": "Zig",
    "nim": "Nim",
    "hs": "Haskell",
    "haskell": "Haskell",
    "ex": "Elixir",
    "exs": "Elixir",
    "elixir": "Elixir",
    "erl": "Erlang",
    "erlang": "Erlang",
    "ml": "OCaml",
    "ocaml": "OCaml",
    "clj": "Clojure",
    "clojure": "Clojure",
    "scala": "Scala",
    "dart": "Dart",
    "r": "R",
    "jl": "Julia",
    "julia": "Julia",
    "nix": "Nix",
    "pl": "Perl",
    "perl": "Perl",
    "sql": "SQL",
    "html": "HTML",
    "css": "CSS",
    "scss": "SCSS",
    "vue": "Vue",
    "svelte": "Svelte",
    "tex": "TeX",
    "matlab": "MATLAB",
    "fortran": "Fortran",
    "asm": "Assembly",
    "assembly": "Assembly",
    "ps1": "PowerShell",
    "powershell": "PowerShell",
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "jupyter": "Jupyter Notebook",
    "notebook": "Jupyter Notebook",
    "groovy": "Groovy",
    "solidity": "Solidity",
    "objc": "Objective-C",
    "objective-c": "Objective-C",
    "objectivec": "Objective-C",
}

_CANONICAL_LANGUAGES: dict[str, str] = {
    name.casefold(): name for name in set(_LANGUAGE_ALIASES.values())
}


def resolve_language(raw: str) -> str:
    """Resolve a user-supplied language to its canonical GitHub name.

    ``py`` becomes ``Python``; an unknown value raises ValueError with
    close matches instead of silently returning an empty result set.

    Raises:
        ValueError: when the value is not a known GitHub language.
    """
    value = raw.strip()
    if not value:
        raise ValueError("Empty language filter")
    alias = _LANGUAGE_ALIASES.get(value.casefold())
    if alias is not None:
        return alias
    suggestion = _CANONICAL_LANGUAGES.get(value.casefold())
    if suggestion is not None:
        return suggestion
    known = sorted(_CANONICAL_LANGUAGES)
    close = get_close_matches(value.casefold(), known, n=3, cutoff=0.6)
    hint = ", ".join(_CANONICAL_LANGUAGES[c] for c in close)
    message = f"Unknown GitHub language: {raw!r}"
    if hint:
        message += f" (did you mean: {hint}?)"
    raise ValueError(message)


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

# Where GitHub should look. "auto" is GitHub's default: name, description
# and topics — never the README. README search is a recall bomb (measured:
# `vega mcp in:readme` returns 977 repositories, nearly all unrelated).
_IN_QUALIFIERS: dict[str, str | None] = {
    "auto": None,
    "name": "in:name",
    "readme": "in:readme",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SearchSpec:
    """Everything needed to build the GitHub query and filter its results."""

    terms: tuple[str, ...]
    language: str | None = None
    in_fields: str = "auto"
    topics: tuple[str, ...] = ()
    not_terms: tuple[str, ...] = ()
    min_stars: int = 0
    pushed_since: str | None = None
    include_forks: bool = False
    include_archived: bool = False

    def to_query(self) -> str:
        """Build the GitHub search query string.

        Raises:
            ValueError: on an invalid date or an unknown ``--in`` value.
        """
        parts: list[str] = [term for term in self.terms if term.strip()]
        if not parts:
            raise ValueError("Empty search query")

        qualifier = _IN_QUALIFIERS.get(self.in_fields)
        if self.in_fields not in _IN_QUALIFIERS:
            raise ValueError(f"Unknown --in value: {self.in_fields!r}")
        if qualifier:
            parts.append(qualifier)

        if self.language:
            parts.append(f"language:{self.language}")
        for topic in self.topics:
            parts.append(f"topic:{topic}")
        if self.min_stars > 0:
            parts.append(f"stars:>={self.min_stars}")
        if self.pushed_since:
            if not _DATE_RE.match(self.pushed_since):
                raise ValueError(
                    f"Invalid --pushed-since date: {self.pushed_since!r} "
                    "(expected YYYY-MM-DD)"
                )
            parts.append(f"pushed:>={self.pushed_since}")
        if self.include_forks:
            parts.append("fork:true")
        if not self.include_archived:
            parts.append("archived:false")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")

# "Mirror of https://github.com/owner/repo" — the publisher says it is a
# copy, so it is not an independent project to score.
_MIRROR_RE = re.compile(r"^(?:mirror|copy|clone)\s+of\b", re.IGNORECASE)

# Rejection reasons, as i18n keys.
REJECT_NOT = "search_reject_not"
REJECT_MIRROR = "search_reject_mirror"
REJECT_DUPLICATE = "search_reject_duplicate"


@dataclass(frozen=True)
class SearchHit:
    """One candidate returned by GitHub, before any analysis."""

    full_name: str
    owner: str
    repo: str
    description: str
    topics: tuple[str, ...]
    language: str
    stars: int
    pushed_at: datetime | None
    created_at: datetime | None
    archived: bool
    forklike: bool

    @classmethod
    def from_api(cls, item: dict) -> SearchHit | None:
        """Build a hit from a REST search item; None when it is unusable."""
        full_name = item.get("full_name") or ""
        if "/" not in full_name:
            return None
        owner, _, repo = full_name.partition("/")
        if not owner or not repo:
            return None
        return cls(
            full_name=full_name,
            owner=owner,
            repo=repo,
            description=(item.get("description") or "").strip(),
            topics=tuple(item.get("topics") or ()),
            language=item.get("language") or "",
            stars=int(item.get("stargazers_count") or 0),
            pushed_at=_parse_datetime(item.get("pushed_at")),
            created_at=_parse_datetime(item.get("created_at")),
            archived=bool(item.get("archived")),
            forklike=bool(item.get("fork")),
        )

    @property
    def repo_url(self) -> RepoUrl:
        """The candidate as a validated GitHub URL, ready for analysis."""
        return RepoUrl(owner=self.owner, repo=self.repo)

    @property
    def html_url(self) -> str:
        return self.repo_url.html_url

    def words(self) -> set[str]:
        """Lowercased word tokens from name, description and topics."""
        text = " ".join((self.repo, self.description, *self.topics))
        return {word for word in _WORD_SPLIT.split(text.casefold()) if word}


def _parse_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp from the API, tolerating junk."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _term_words(term: str) -> set[str]:
    """Split a search term or exclusion into word tokens."""
    return {word for word in _WORD_SPLIT.split(term.casefold()) if word}


@dataclass(frozen=True)
class Rejection:
    """A candidate dropped by the funnel, with an auditable reason."""

    full_name: str
    reason: str
    detail: str = ""


@dataclass
class Funnel:
    """Candidates that survived the funnel, plus every drop and its reason."""

    total_count: int
    incomplete: bool
    kept: list[SearchHit] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    def count(self, reason: str) -> int:
        """Number of drops for one reason key."""
        return sum(1 for rejection in self.rejected if rejection.reason == reason)

    @property
    def found(self) -> int:
        """Candidates actually examined (the API may report more)."""
        return len(self.kept) + len(self.rejected)


def _duplicate_signature(hit: SearchHit) -> tuple[str, str] | None:
    """Identity used to collapse the same repository published twice.

    Two repositories with the same description and the same name (modulo
    the owner segment) are one project: spammers republish under several
    organisations within seconds.
    """
    if not hit.description:
        return None
    name = re.sub(r"[^a-z0-9]", "", hit.repo.casefold())
    description = " ".join(_WORD_SPLIT.split(hit.description.casefold()))
    return name, description


def apply_funnel(
    hits: list[SearchHit],
    spec: SearchSpec,
    *,
    total_count: int = 0,
    incomplete: bool = False,
    dedupe: bool = True,
) -> Funnel:
    """Run the deterministic relevance funnel over raw candidates.

    Only knowable drops happen here: explicit exclusions, declared
    mirrors, duplicates. GitHub's lexical noise is deliberately *not*
    filtered — see the module docstring for the measurement that ruled
    it out. Candidates keep the API order, which is GitHub relevance or
    the requested sort.
    """
    funnel = Funnel(total_count=total_count or len(hits), incomplete=incomplete)
    # Keep (term, tokens) pairs so a blank term cannot shift the mapping.
    # Exclusions match on whole words only: "--not vegas" must not eat
    # "vegalite", and "--not vega-os" is a phrase (every word required).
    not_words = [
        (term, tokens) for term in spec.not_terms if (tokens := _term_words(term))
    ]
    seen: dict[tuple[str, str], str] = {}

    for hit in hits:
        words = hit.words()

        excluded = next(
            (term for term, tokens in not_words if tokens <= words),
            None,
        )
        if excluded is not None:
            funnel.rejected.append(Rejection(hit.full_name, REJECT_NOT, excluded))
            continue

        if _MIRROR_RE.match(hit.description):
            funnel.rejected.append(Rejection(hit.full_name, REJECT_MIRROR))
            continue

        if dedupe:
            signature = _duplicate_signature(hit)
            if signature is not None:
                first = seen.get(signature)
                if first is not None:
                    funnel.rejected.append(
                        Rejection(hit.full_name, REJECT_DUPLICATE, first)
                    )
                    continue
                seen[signature] = hit.full_name

        funnel.kept.append(hit)

    return funnel
