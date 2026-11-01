"""Tests for GitHub search: query building, funnel, dedupe, fetch.

No network: the funnel is pure, and the fetcher test mocks httpx.
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rich.console import Console

from gh_score.cli.main import (
    _funnel_payload,
    _hit_payload,
    _render_search_json,
    _render_search_markdown,
)
from gh_score.cli.tui import render_triage
from gh_score.core.fetchers.github import GitHubFetcher
from gh_score.core.models import (
    AnalysisResult,
    ContributorsIndicator,
    LanguagesIndicator,
    LicenseIndicator,
    MaintenanceIndicator,
    MaintenanceState,
    Recommendation,
    RecommendationLevel,
    ReleaseHealthIndicator,
    RepositoryMeta,
    RepoUrl,
    SustainabilityIndicator,
)
from gh_score.core.search import (
    REJECT_DUPLICATE,
    REJECT_MIRROR,
    REJECT_NOT,
    Funnel,
    SearchHit,
    SearchSpec,
    apply_funnel,
    resolve_languages,
)


def _item(**overrides) -> dict:
    """A GitHub search item, shaped like the real REST payload."""
    base = {
        "full_name": "owner/repo",
        "description": "",
        "topics": [],
        "language": "Python",
        "stargazers_count": 0,
        "pushed_at": "2026-01-04T13:59:47Z",
        "created_at": "2025-01-04T13:59:47Z",
        "archived": False,
        "fork": False,
    }
    base.update(overrides)
    return base


def _hit(**overrides) -> SearchHit:
    hit = SearchHit.from_api(_item(**overrides))
    assert hit is not None
    return hit


class TestLanguageResolution:
    def test_short_alias(self):
        assert resolve_languages(("py",)) == ("Python",)

    def test_alias_is_case_insensitive(self):
        assert resolve_languages(("PY",)) == ("Python",)

    def test_canonical_name_passes_through(self):
        assert resolve_languages(("TypeScript",)) == ("TypeScript",)

    def test_other_aliases(self):
        assert resolve_languages(("rs",)) == ("Rust",)
        assert resolve_languages(("golang",)) == ("Go",)
        assert resolve_languages(("csharp",)) == ("C#",)

    def test_comma_separated_values(self):
        assert resolve_languages(("py,rust",)) == ("Python", "Rust")

    def test_repeated_option(self):
        assert resolve_languages(("py", "rust")) == ("Python", "Rust")

    def test_full_ecosystem_family(self):
        # "js" names the ecosystem: GitHub ORs the repeated qualifiers, so
        # a tool migrated to TypeScript is not lost.
        assert resolve_languages(("js",)) == ("JavaScript", "TypeScript")
        assert resolve_languages(("node",)) == ("JavaScript", "TypeScript")

    def test_explicit_ecosystem_member_stays_single(self):
        assert resolve_languages(("ts",)) == ("TypeScript",)
        assert resolve_languages(("javascript",)) == ("JavaScript",)

    def test_duplicates_are_collapsed(self):
        assert resolve_languages(("py,Python,python3",)) == ("Python",)

    def test_unknown_language_raises_with_suggestion(self):
        with pytest.raises(ValueError, match="did you mean: Python"):
            resolve_languages(("pythn",))

    def test_unknown_language_without_close_match(self):
        with pytest.raises(ValueError, match="Unknown GitHub language"):
            resolve_languages(("zzzznotalanguage",))

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty language"):
            resolve_languages(("   ",))


class TestQueryBuilding:
    def test_archived_excluded_by_default(self):
        query = SearchSpec(terms=("vega", "mcp")).to_query()
        assert query == "vega mcp archived:false"

    def test_language_and_extra_qualifiers(self):
        spec = SearchSpec(
            terms=("vega", "mcp"),
            languages=("Python",),
            topics=("vega-lite",),
            min_stars=5,
            pushed_since="2025-01-31",
        )
        assert spec.to_query() == (
            "vega mcp language:Python topic:vega-lite "
            "stars:>=5 pushed:>=2025-01-31 archived:false"
        )

    def test_in_name_qualifier(self):
        assert SearchSpec(terms=("vega",), in_fields="name").to_query() == (
            "vega in:name archived:false"
        )

    def test_auto_adds_no_in_qualifier(self):
        # GitHub's default: name, description, topics — never the README.
        assert "in:" not in SearchSpec(terms=("vega",)).to_query()

    def test_forks_and_archived_can_be_included(self):
        spec = SearchSpec(terms=("vega",), include_forks=True, include_archived=True)
        assert spec.to_query() == "vega fork:true"

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError, match="Invalid --pushed-since"):
            SearchSpec(terms=("vega",), pushed_since="31/01/2025").to_query()

    def test_unknown_in_value_raises(self):
        with pytest.raises(ValueError, match="Unknown --in value"):
            SearchSpec(terms=("vega",), in_fields="description").to_query()

    def test_empty_terms_raise(self):
        with pytest.raises(ValueError, match="Empty search query"):
            SearchSpec(terms=()).to_query()


class TestSearchHit:
    def test_words_include_name_description_and_topics(self):
        hit = _hit(
            full_name="acme/vega-flume",
            description="Model Context Protocol for charts",
            topics=["mcp", "vega-lite"],
        )
        assert {"vega", "flume", "mcp", "charts", "model", "context", "protocol"} <= (
            hit.words()
        )

    def test_missing_full_name_is_unusable(self):
        assert SearchHit.from_api({"name": "orphan"}) is None

    def test_html_url(self):
        assert _hit(full_name="acme/vega").html_url == "https://github.com/acme/vega"

    def test_bad_timestamp_becomes_none(self):
        hit = _hit(pushed_at="not-a-date")
        assert hit.pushed_at is None

    def test_payload_is_serializable(self):
        payload = _hit_payload(_hit(full_name="acme/vega", topics=["mcp"]))
        assert payload["url"] == "https://github.com/acme/vega"
        assert payload["topics"] == ["mcp"]
        assert payload["pushed_at"] == "2026-01-04T13:59:47+00:00"


class TestFunnelLexicalNoise:
    """Lexical noise is deliberately *not* filtered.

    Measured: GitHub matches `vega` against `vegalite` (the best hit) and
    against `vegas` (noise) with the same prefix rule, so any filter that
    drops one drops the other. `--not` is the deterministic lever instead.
    """

    def test_compound_match_of_a_relevant_project_is_kept(self):
        # "mcp-vegalite-server" is the single most relevant hit of the
        # measured sample: a word-exact filter would delete it.
        kept = apply_funnel(
            [_hit(full_name="isaacwasserman/mcp-vegalite-server")],
            SearchSpec(terms=("vega", "mcp")),
        ).kept
        assert [hit.full_name for hit in kept] == ["isaacwasserman/mcp-vegalite-server"]

    def test_compound_noise_is_kept_too(self):
        kept = apply_funnel(
            [_hit(full_name="rkohnmn/qwen-vegas-agent")],
            SearchSpec(terms=("vega", "mcp")),
        ).kept
        assert len(kept) == 1

    def test_not_distinguishes_the_two(self):
        hits = [
            _hit(full_name="isaacwasserman/mcp-vegalite-server"),
            _hit(full_name="rkohnmn/qwen-vegas-agent"),
        ]
        kept = apply_funnel(
            hits, SearchSpec(terms=("vega",), not_terms=("vegas",))
        ).kept
        assert [hit.full_name for hit in kept] == ["isaacwasserman/mcp-vegalite-server"]

    def test_topic_match_counts(self):
        kept = apply_funnel(
            [_hit(full_name="acme/charts", topics=["vega", "mcp"])],
            SearchSpec(terms=("vega", "mcp")),
        ).kept
        assert len(kept) == 1


class TestFunnelExclusions:
    def test_not_removes_a_whole_word(self):
        funnel = apply_funnel(
            [
                _hit(
                    full_name="vvdotcom/Hometide",
                    description="Family hub for Fire TV (Vega OS) built with MCP.",
                )
            ],
            SearchSpec(terms=("vega", "mcp"), not_terms=("vega-os",)),
        )
        assert funnel.kept == []
        assert funnel.rejected[0].reason == REJECT_NOT
        assert funnel.rejected[0].detail == "vega-os"

    def test_not_needs_every_word_of_the_phrase(self):
        # "new vegas" must not exclude a repository that only says "vega".
        kept = apply_funnel(
            [_hit(full_name="acme/vega-tools", description="Vega rendering")],
            SearchSpec(terms=("vega",), not_terms=("new vegas",)),
        ).kept
        assert len(kept) == 1

    def test_multiple_not_terms(self):
        funnel = apply_funnel(
            [
                _hit(full_name="a/one", description="vega greeks engine"),
                _hit(full_name="b/two", description="vega dashboards"),
            ],
            SearchSpec(terms=("vega",), not_terms=("greeks", "vegas")),
        )
        assert [hit.full_name for hit in funnel.kept] == ["b/two"]


class TestFunnelStructural:
    def test_declared_mirror_is_rejected(self):
        funnel = apply_funnel(
            [
                _hit(
                    full_name="Mavline/isaacwasserman_mcp-vegalite-server",
                    description="Mirror of https://github.com/isaacwasserman/x",
                )
            ],
            SearchSpec(terms=("vega", "mcp")),
        )
        assert funnel.rejected[0].reason == REJECT_MIRROR

    def test_same_project_under_two_owners_is_deduped(self):
        funnel = apply_funnel(
            [
                _hit(
                    full_name="Alpha-Park/genpark-greeks",
                    description="Greeks (Delta, Gamma, Vega) engine.",
                ),
                _hit(
                    full_name="alphaparkinc/genpark-greeks",
                    description="Greeks (Delta, Gamma, Vega) engine.",
                ),
            ],
            SearchSpec(terms=("vega",)),
        )
        assert [hit.full_name for hit in funnel.kept] == ["Alpha-Park/genpark-greeks"]
        assert funnel.rejected[0].reason == REJECT_DUPLICATE
        assert funnel.rejected[0].detail == "Alpha-Park/genpark-greeks"

    def test_dedup_can_be_disabled(self):
        funnel = apply_funnel(
            [
                _hit(full_name="a/same", description="one description"),
                _hit(full_name="b/same", description="one description"),
            ],
            SearchSpec(terms=()),
            dedupe=False,
        )
        assert len(funnel.kept) == 2

    def test_empty_description_is_never_deduped(self):
        # Without a description there is no evidence the two are the same.
        funnel = apply_funnel(
            [_hit(full_name="a/tools"), _hit(full_name="b/tools")],
            SearchSpec(terms=()),
        )
        assert len(funnel.kept) == 2


class TestFunnelBookkeeping:
    def test_counts_and_order(self):
        hits = [
            _hit(full_name="a/vega-mcp"),
            _hit(full_name="b/vega-os-tools"),
            _hit(full_name="c/vega-mcp-other", description="Mirror of a/x"),
        ]
        spec = SearchSpec(terms=("vega", "mcp"), not_terms=("vega-os",))
        funnel = apply_funnel(hits, spec, total_count=42)
        assert [hit.full_name for hit in funnel.kept] == ["a/vega-mcp"]
        assert funnel.total_count == 42
        assert funnel.found == 3
        assert funnel.count(REJECT_NOT) == 1
        assert funnel.count(REJECT_MIRROR) == 1

    def test_incomplete_flag_is_kept(self):
        funnel = apply_funnel([], SearchSpec(terms=("x",)), incomplete=True)
        assert funnel.incomplete is True

    def test_payload_shape(self):
        funnel = apply_funnel(
            [_hit(full_name="b/vega-os")],
            SearchSpec(terms=("vega",), not_terms=("vega-os",)),
        )
        payload = _funnel_payload(funnel)
        assert payload["kept"] == 0
        assert payload["rejected"][0] == {
            "repo": "b/vega-os",
            "reason": REJECT_NOT,
            "detail": "vega-os",
        }


class TestSearchFetcher:
    def _fetcher(self, tmp_path) -> GitHubFetcher:
        from gh_score.config import Config
        from gh_score.core.cache import Cache

        config = Config()
        config.github.token = "test-token"
        return GitHubFetcher(config, Cache(str(tmp_path)))

    @pytest.mark.asyncio
    async def test_envelope_is_returned_as_is(self, tmp_path):
        fetcher = self._fetcher(tmp_path)
        envelope = {"total_count": 1, "incomplete_results": False, "items": []}
        fetcher._get = AsyncMock(return_value=envelope)  # pylint: disable=protected-access
        try:
            result = await fetcher.search_repositories("vega mcp", per_page=20)
        finally:
            await fetcher.close()
        assert result == envelope

    @pytest.mark.asyncio
    async def test_query_and_sort_are_forwarded(self, tmp_path):
        fetcher = self._fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={"items": []})  # pylint: disable=protected-access
        try:
            await fetcher.search_repositories("vega", per_page=5, sort="stars")
        finally:
            await fetcher.close()
        _url, params = fetcher._get.await_args.args  # pylint: disable=protected-access
        assert params == {
            "q": "vega",
            "per_page": "5",
            "sort": "stars",
            "order": "desc",
        }

    @pytest.mark.asyncio
    async def test_no_sort_means_no_order(self, tmp_path):
        fetcher = self._fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value={"items": []})  # pylint: disable=protected-access
        try:
            await fetcher.search_repositories("vega")
        finally:
            await fetcher.close()
        _url, params = fetcher._get.await_args.args  # pylint: disable=protected-access
        assert "sort" not in params and "order" not in params

    @pytest.mark.asyncio
    async def test_failure_returns_none(self, tmp_path):
        fetcher = self._fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value=None)  # pylint: disable=protected-access
        try:
            assert await fetcher.search_repositories("vega") is None
        finally:
            await fetcher.close()

    @pytest.mark.asyncio
    async def test_non_dict_answer_is_a_failure(self, tmp_path):
        # A list would mean an unexpected endpoint shape: refuse it rather
        # than iterating over whatever came back.
        fetcher = self._fetcher(tmp_path)
        fetcher._get = AsyncMock(return_value=[])  # pylint: disable=protected-access
        try:
            assert await fetcher.search_repositories("vega") is None
        finally:
            await fetcher.close()


class TestFunnelWithRealWorldSample:
    """The measured 17-result sample for `vega mcp language:python`."""

    SAMPLE = [
        ("isaacwasserman/mcp-vegalite-server", "", []),
        (
            "Alpha-Park/genpark-greeks",
            "Greeks (Delta, Gamma, Vega, Theta, Rho) engine.",
            ["mcp"],
        ),
        (
            "alphaparkinc/genpark-greeks",
            "Greeks (Delta, Gamma, Vega, Theta, Rho) engine.",
            ["mcp"],
        ),
        ("ihgs/vega_png_mcp", "", []),
        ("jonmmease/jons-mcp-vega", "An MCP Server for Rendering Vega Charts", []),
        (
            "RFingAdam/vegas-director-mcp",
            "MCP server for driving MAGIX VEGAS Pro",
            [],
        ),
        ("Mavline/mcp-server-vegalite", "LLM data visualization with Vega-Lite.", []),
        (
            "Mavline/isaacwasserman_mcp-vegalite-server",
            "Mirror of https://github.com/isaacwasserman/mcp-vegalite-server",
            [],
        ),
        (
            "jdhwjdvsu-web/Vegapunk-Fluent",
            "Vegapunk integration with PyFluent-MCP",
            [],
        ),
        ("dosquartsdedocs/vegavisuals", "Vega and Vega-Lite rendering.", []),
        ("rkohnmn/qwen-vegas-agent", "Personal project", []),
        (
            "gavinbrow/Decompilation-MCP-Server",
            "MCP tools for Fallout: New Vegas decompilation",
            [],
        ),
        (
            "emarc99/ChronoGrid",
            "Home camera hub on Vega OS with an MCP server",
            ["vega"],
        ),
        (
            "vvdotcom/Hometide",
            "Family hub for Fire TV (Vega OS) built with MCP.",
            ["vega-os"],
        ),
    ]

    def _hits(self) -> list[SearchHit]:
        hits = []
        for name, description, topics in self.SAMPLE:
            hit = SearchHit.from_api(
                _item(full_name=name, description=description, topics=topics)
            )
            assert hit is not None
            hits.append(hit)
        return hits

    def test_only_structural_noise_is_dropped(self):
        funnel = apply_funnel(self._hits(), SearchSpec(terms=("vega", "mcp")))
        kept = {hit.full_name for hit in funnel.kept}
        # 14 candidates in, 2 structural drops out: the declared mirror and
        # the same repository published under two organisations.
        assert len(kept) == 12
        assert "Mavline/isaacwasserman_mcp-vegalite-server" not in kept
        assert "alphaparkinc/genpark-greeks" not in kept
        assert funnel.count(REJECT_MIRROR) == 1
        assert funnel.count(REJECT_DUPLICATE) == 1
        # Lexical noise survives on purpose: it is the price of keeping
        # "vegalite", and --not is how the user removes it.
        assert "rkohnmn/qwen-vegas-agent" in kept
        assert "isaacwasserman/mcp-vegalite-server" in kept

    def test_user_exclusions_remove_the_polysemy(self):
        funnel = apply_funnel(
            self._hits(),
            SearchSpec(
                terms=("vega", "mcp"),
                not_terms=("vega-os", "greeks", "vegas"),
            ),
        )
        kept = {hit.full_name for hit in funnel.kept}
        # 17-result sample, 3 measured words: 6 relevant projects left.
        assert kept == {
            "isaacwasserman/mcp-vegalite-server",
            "ihgs/vega_png_mcp",
            "jonmmease/jons-mcp-vega",
            "Mavline/mcp-server-vegalite",
            "dosquartsdedocs/vegavisuals",
            "jdhwjdvsu-web/Vegapunk-Fluent",
        }
        # Documented limit: "--not vegas" is a word match, so it never
        # eats "vegalite" — and "vegapunk" needs its own exclusion.
        assert "jdhwjdvsu-web/Vegapunk-Fluent" in kept


class TestSearchCommand:
    """End-to-end wiring with the network call mocked out."""

    ENVELOPE = {
        "total_count": 3,
        "incomplete_results": False,
        "items": [
            _item(full_name="acme/vega-mcp", description="Vega charts over MCP"),
            _item(
                full_name="acme/vega-os-hub",
                description="Fire TV companion",
                topics=["vega-os"],
            ),
            _item(full_name="acme/mirror", description="Mirror of acme/vega-mcp"),
        ],
    }

    def _run(self, *args):
        from click.testing import CliRunner

        from gh_score.cli.main import cli

        with patch("gh_score.cli.main._run_search", return_value=self.ENVELOPE):
            return CliRunner().invoke(cli, ["search", *args])

    def test_no_analyze_lists_candidates_without_health(self):
        result = self._run("vega", "mcp", "--no-analyze", "--lang", "py")
        assert result.exit_code == 0, result.output
        assert "acme/vega-mcp" in result.output
        # The funnel explains the drop instead of hiding it.
        assert "acme/mirror" in result.output

    def test_json_format_reports_the_funnel(self):
        result = self._run("vega", "mcp", "--no-analyze", "--format", "json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["query"] == "vega mcp archived:false"
        assert payload["funnel"]["kept"] == 2
        assert payload["funnel"]["rejected"][0]["reason"] == "search_reject_mirror"
        assert len(payload["candidates"]) == 2

    def test_unknown_language_fails_with_a_hint(self):
        result = self._run("vega", "--no-analyze", "--lang", "pythn")
        assert result.exit_code == 1
        assert "did you mean: Python" in result.output

    def test_not_option_is_forwarded_to_the_funnel(self):
        result = self._run(
            "vega", "mcp", "--no-analyze", "--not", "vega-os", "--format", "json"
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        kept = [candidate["full_name"] for candidate in payload["candidates"]]
        assert kept == ["acme/vega-mcp"]

    def test_health_sort_requires_analysis(self):
        result = self._run("vega", "--no-analyze", "--sort", "health")
        assert result.exit_code == 1
        assert "--sort health" in result.output

    def test_search_failure_is_reported_not_silent(self):
        from click.testing import CliRunner

        from gh_score.cli.main import cli

        with patch("gh_score.cli.main._run_search", return_value=None):
            result = CliRunner().invoke(cli, ["search", "vega", "--no-analyze"])
        assert result.exit_code == 1
        assert "GitHub search failed" in result.output

    def test_missing_token_warns_about_the_cost(self):
        from click.testing import CliRunner

        from gh_score.cli import main as cli_main
        from gh_score.cli.main import cli as cli_command

        config = MagicMock()
        config.llm.enabled = False
        config.github.token = ""
        analysis = [_analysis("acme/vega-mcp", 1, RecommendationLevel.GREEN)]
        with (
            patch.object(cli_main, "_prepare_config", return_value=config),
            patch.object(cli_main, "_run_search", return_value=self.ENVELOPE),
            patch.object(
                cli_main, "_gather_analyses", new=AsyncMock(return_value=analysis)
            ),
        ):
            result = CliRunner().invoke(
                cli_command, ["search", "vega", "mcp", "--limit", "1"]
            )
        assert result.exit_code == 0, result.output
        assert "60 requests/hour" in result.output


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, width=100, force_terminal=False), buffer


def _analysis(full_name: str, stars: int, level: RecommendationLevel) -> AnalysisResult:
    """Minimal analyzed result for the triage renderer."""
    owner, _, repo = full_name.partition("/")
    return AnalysisResult(
        url=RepoUrl(owner, repo),
        meta=RepositoryMeta(full_name=full_name, stars=stars),
        release_health=ReleaseHealthIndicator(),
        license=LicenseIndicator(spdx_id="MIT"),
        contributors=ContributorsIndicator(),
        maintenance=MaintenanceIndicator(
            state=MaintenanceState.ACTIVE, last_commit_days_ago=3
        ),
        languages=LanguagesIndicator(primary="Python"),
        sustainability=SustainabilityIndicator(),
        recommendation=Recommendation(level=level, message="ok"),
    )


class TestTriageRenderer:
    def _funnel(self, *, with_results: bool = False) -> Funnel:
        return apply_funnel(
            [
                _hit(full_name="acme/vega-mcp", description="Vega charts over MCP"),
                _hit(full_name="acme/vega-os", description="Fire TV hub"),
            ],
            SearchSpec(terms=("vega", "mcp"), not_terms=("vega-os",)),
        )

    def test_candidates_view_lists_projects_and_drops(self):
        console, buffer = _console()
        render_triage(self._funnel(), "vega mcp", "relevance", console=console)
        output = buffer.getvalue()
        assert "acme/vega-mcp" in output
        assert "acme/vega-os" in output  # the drop is printed, not hidden
        assert "excluded by --not" in output

    def test_results_view_shows_the_verdict(self):
        console, buffer = _console()
        results = [_analysis("acme/vega-mcp", 100, RecommendationLevel.GREEN)]
        render_triage(
            self._funnel(), "vega mcp", "health verdict", results, console=console
        )
        output = buffer.getvalue()
        assert "🟢" in output
        assert "Relevance was decided by the filter above" in output

    def test_limit_is_stated_when_fewer_projects_are_analyzed(self):
        console, buffer = _console()
        hits = [
            _hit(full_name="acme/vega-mcp"),
            _hit(full_name="acme/vega-charts"),
        ]
        funnel = apply_funnel(hits, SearchSpec(terms=("vega",)))
        results = [_analysis("acme/vega-mcp", 10, RecommendationLevel.GREEN)]
        render_triage(funnel, "vega", "relevance", results, console=console)
        assert "Analyzed 1 of 2 kept" in buffer.getvalue()

    def test_rejection_list_is_capped_but_counted(self):
        console, buffer = _console()
        hits = [_hit(full_name=f"acme/vega-os-{n}") for n in range(20)]
        funnel = apply_funnel(hits, SearchSpec(terms=("vega",), not_terms=("vega-os",)))
        render_triage(funnel, "vega", "relevance", console=console)
        output = buffer.getvalue()
        assert "… and 8 more" in output
        assert "acme/vega-os-19" not in output

    def test_narrow_console_keeps_every_column(self):
        console = Console(file=io.StringIO(), width=80, force_terminal=False)
        render_triage(self._funnel(), "vega mcp", "relevance", console=console)
        output = console.file.getvalue()
        assert "Stars" in output
        assert "Language" in output
        assert "Updated" in output


class TestSearchRenderers:
    def _funnel(self) -> Funnel:
        return apply_funnel(
            [_hit(full_name="acme/vega-mcp", description="Vega over MCP")],
            SearchSpec(terms=("vega", "mcp"), languages=("Python",)),
        )

    def test_json_contains_funnel_and_projects(self):
        console, buffer = _console()
        results = [_analysis("acme/vega-mcp", 100, RecommendationLevel.GREEN)]
        _render_search_json(
            self._funnel(),
            SearchSpec(terms=("vega", "mcp"), languages=("Python",)),
            "vega mcp language:Python",
            results,
            console,
        )
        payload = json.loads(buffer.getvalue())
        assert payload["languages"] == ["Python"]
        assert payload["funnel"]["kept"] == 1
        assert payload["ranking"] == ["acme/vega-mcp"]
        assert payload["projects"][0]["meta"]["stars"] == 100

    def test_json_without_analysis_has_no_projects(self):
        console, buffer = _console()
        _render_search_json(
            self._funnel(),
            SearchSpec(terms=("vega",)),
            "vega",
            None,
            console,
        )
        payload = json.loads(buffer.getvalue())
        assert "projects" not in payload
        assert payload["candidates"][0]["full_name"] == "acme/vega-mcp"

    def test_markdown_lists_candidates_before_analysis(self):
        console, buffer = _console()
        _render_search_markdown(
            self._funnel(),
            SearchSpec(terms=("vega",)),
            "vega",
            None,
            "relevance",
            console,
        )
        output = buffer.getvalue()
        assert "# GitHub Search" in output
        assert "| Project | Stars | Language | Updated | Description |" in output
        assert "acme/vega-mcp" in output

    def test_markdown_table_after_analysis(self):
        console, buffer = _console()
        results = [_analysis("acme/vega-mcp", 100, RecommendationLevel.GREEN)]
        _render_search_markdown(
            self._funnel(),
            SearchSpec(terms=("vega",)),
            "vega",
            results,
            "health",
            console,
        )
        output = buffer.getvalue()
        assert (
            "| Project | Stars | Language | State | Last commit | Verdict |" in output
        )
        assert "Ranking: health verdict" in output
