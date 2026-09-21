"""CLI entry point for gh-score."""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from gh_score.config import Config
from gh_score.core.api import analyze_repo, analyze_repo_async, refine_comparison_subjects
from gh_score.core.analyzers.license_analyzer import license_family_label
from gh_score.core.cache import Cache
from gh_score.core.comparison import (
    ComparisonResult,
    ComparisonVerdict,
    compare_results,
    project_downloads,
    ranked_projects,
)
from gh_score.core.models import AnalysisResult, RecommendationLevel, RepoUrl
from gh_score.i18n import t
from gh_score.cli.tui import render_comparison, render_dashboard


# ---------------------------------------------------------------------------
# URL / path resolution
# ---------------------------------------------------------------------------


def _resolve_target(url_or_path: str | None, force_remote: bool = False) -> tuple[str | None, bool]:
    """Determine what to analyze and whether to use local mode.

    Returns:
        (resolved_path, use_local)
    """
    if url_or_path is not None:
        path = Path(url_or_path)
        if path.exists() and (path / ".git").exists() and not force_remote:
            return str(path), True
        return url_or_path, False

    # No argument provided: try current directory
    if force_remote:
        return None, False

    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return str(cwd), True

    return None, False


def _validate_url(url_or_path: str, local: bool, console: Console) -> None:
    """Validate a GitHub URL when not in local mode."""
    if local or Path(url_or_path).exists():
        return

    try:
        RepoUrl.parse(url_or_path)
    except ValueError as exc:
        console.print(f"[red]{t('cli_error')}[/red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _prepare_config(config_path: str | None, refresh: bool, no_llm: bool, console: Console) -> Config:
    """Load config and apply CLI overrides."""
    config = Config.load(config_path)

    if refresh:
        cache = Cache(config.cache.dir)
        cache.clear()
        console.print(f"[dim]{t('cli_cache_cleared')}[/dim]")

    if no_llm:
        config.llm.enabled = False

    return config


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _warn_stderr(result: AnalysisResult) -> None:
    """Emit localized warnings on stderr for file-based formats."""
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _render_json(result: AnalysisResult, console: Console) -> None:
    """Render results as JSON."""
    _warn_stderr(result)
    console.print_json(json.dumps(asdict(result), default=str))


_MD_GLYPHS = {
    RecommendationLevel.GREEN: "🟢",
    RecommendationLevel.ORANGE: "🟠",
    RecommendationLevel.RED: "🔴",
}


def _md_recommendation(result: AnalysisResult, console: Console) -> None:
    """Render the traffic-light recommendation as Markdown."""
    rec = result.recommendation
    glyph = _MD_GLYPHS.get(rec.level, "❓")
    console.print(f"{t('md_section_recommendation')}\n")
    console.print(f"{glyph} **{rec.message}**\n")
    for reason in rec.reasoning:
        console.print(f"- {reason}")
    if rec.confidence > 0:
        console.print(f"\n**{t('md_confidence', conf=rec.confidence)}**\n")


def _md_llm_recommendation(result: AnalysisResult, console: Console) -> None:
    """Render the optional LLM refined recommendation as Markdown."""
    rec = result.llm_recommendation
    if not rec or not rec.level:
        return
    try:
        level = RecommendationLevel(rec.level)
    except ValueError:
        return
    glyph = _MD_GLYPHS.get(level, "❓")
    console.print(f"{t('md_section_llm_recommendation')}\n")
    console.print(f"{glyph} **{rec.message}**\n")
    if rec.explanation:
        console.print(f"{rec.explanation}\n")
    if rec.confidence > 0:
        console.print(f"**{t('md_confidence', conf=rec.confidence)}**\n")


def _render_markdown(result: AnalysisResult, console: Console) -> None:
    """Render results as Markdown."""
    _warn_stderr(result)
    console.print(f"# GitHub Health Report: {result.url}\n")

    if result.meta.description:
        console.print(f"_{result.meta.description}_\n")

    if result.meta.is_mirror:
        if result.meta.mirror_upstream:
            console.print(
                t("tui_mirror_upstream", upstream=result.meta.mirror_upstream)
            )
        else:
            console.print(t("tui_mirror"))
        console.print()

    if result.meta.fork:
        parent = result.meta.parent_full_name or result.meta.source_full_name or "?"
        if result.meta.is_soft_fork:
            console.print(t("tui_fork_soft", parent=parent))
        else:
            console.print(t("tui_fork_hard", parent=parent))
        if result.meta.fork_prs:
            prs = ", ".join(
                f"#{pr.number} ({pr.state}) {pr.title}" for pr in result.meta.fork_prs
            )
            console.print(t("tui_fork_prs", prs=prs))
        console.print()

    console.print(
        f"{t('md_header_stars', count=result.meta.stars)} | "
        f"{t('md_header_forks', count=result.meta.forks)}"
    )
    if result.meta.owner_type:
        console.print(
            t("md_owner", type=t(f"owner_type_{result.meta.owner_type}"))
        )
    console.print()

    _md_recommendation(result, console)
    _md_llm_recommendation(result, console)
    _md_release_health(result, console)
    _md_license(result, console)
    _md_contributors(result, console)
    _md_maintenance(result, console)
    _md_languages(result, console)
    _md_sustainability(result, console)
    _md_registries(result, console)
    _md_website(result, console)
    _md_security(result, console)
    _md_qualitative(result, console)


def _md_release_health(result: AnalysisResult, console: Console) -> None:
    rh = result.release_health
    console.print(f"{t('md_section_release_health')}\n")
    if rh.latest_version:
        console.print(f"- {t('md_latest', version=rh.latest_version)}")
    if rh.age_days is not None:
        console.print(f"- {t('md_age', days=rh.age_days)}")
    if rh.cadence_days is not None:
        console.print(f"- {t('md_cadence', days=rh.cadence_days)}")
    console.print(f"- {t('md_status', status=t(f'status_{rh.status.value}'))}\n")


def _md_license(result: AnalysisResult, console: Console) -> None:
    lic = result.license
    console.print(f"{t('md_section_license')}\n")
    if lic.spdx_id:
        console.print(
            f"- {t('md_license_label', spdx=lic.spdx_id, family=license_family_label(lic.family))}"
        )
    else:
        console.print(f"- {t('md_license_none')}")
    console.print(f"- {t('md_status', status=t(f'status_{lic.status.value}'))}\n")


def _md_contributors(result: AnalysisResult, console: Console) -> None:
    contrib = result.contributors
    console.print(f"{t('md_section_contributors')}\n")
    console.print(f"- {t('md_total_authors', count=contrib.total_authors)}")
    console.print(f"- {t('md_bus_factor', count=contrib.bus_factor)}")
    if contrib.bot_ratio > 0:
        console.print(f"- {t('md_bot_ratio', ratio=contrib.bot_ratio)}")
    if contrib.lead:
        console.print(f"- {t('md_lead', login=contrib.lead.login)}")
    console.print(f"- {t('md_status', status=t(f'status_{contrib.status.value}'))}\n")


def _md_maintenance(result: AnalysisResult, console: Console) -> None:
    maint = result.maintenance
    console.print(f"{t('md_section_maintenance')}\n")
    console.print(f"- {t('md_state', state=t(f'state_{maint.state.value}'))}")
    if maint.last_commit_days_ago is not None:
        console.print(f"- {t('md_last_commit', days=maint.last_commit_days_ago)}")
    if maint.commits_per_month is not None:
        console.print(f"- {t('md_frequency', rate=maint.commits_per_month)}")
    console.print(f"- {t('md_status', status=t(f'status_{maint.status.value}'))}\n")


def _md_languages(result: AnalysisResult, console: Console) -> None:
    lang = result.languages
    console.print(f"{t('md_section_languages')}\n")
    if lang.primary:
        console.print(f"- {t('md_primary', language=lang.primary)}")
    if lang.breakdown:
        console.print(f"- {t('md_breakdown')}")
        sorted_langs = sorted(lang.breakdown.items(), key=lambda x: x[1], reverse=True)[:5]
        for lang_name, pct in sorted_langs:
            console.print(f"  - {lang_name}: {pct:.1f}%\n")
    if lang.interpretation:
        console.print(f"- {lang.interpretation}")


def _md_sustainability(result: AnalysisResult, console: Console) -> None:
    sust = result.sustainability
    console.print(f"{t('md_section_sustainability')}\n")
    if sust.foundation:
        console.print(f"- {t('md_foundation', name=sust.foundation)}")
    if sust.funding_platforms:
        console.print(
            f"- {t('md_funding', platforms=', '.join(sust.funding_platforms))}"
        )
    if sust.corporate_backing:
        console.print(f"- {t('md_corporate', company=sust.corporate_backing)}")
    console.print(f"- {t('md_status', status=t(f'status_{sust.status.value}'))}\n")


def _md_registries(result: AnalysisResult, console: Console) -> None:
    """Render the package registries section as Markdown."""
    if not result.registries:
        return
    console.print(f"{t('md_section_registries')}\n")
    for reg in result.registries:
        if reg.exists:
            line = f"- **{reg.ecosystem}**: {reg.package_name}"
            if reg.latest_version:
                line += f" @ {reg.latest_version}"
            console.print(line)
            if reg.downloads is not None:
                console.print(f"  - {t('tui_downloads', count=reg.downloads)}")
            if reg.recent_downloads is not None:
                console.print(f"  - {t('tui_recent', count=reg.recent_downloads)}")
            if reg.dependents is not None:
                console.print(f"  - {t('tui_dependents', count=reg.dependents)}")
            if reg.registry_license:
                console.print(f"  - {t('tui_registry_license', license=reg.registry_license)}")
            if reg.deprecated:
                console.print(f"  - {t('tui_deprecated')}")
        else:
            console.print(f"- **{reg.ecosystem}**: {reg.package_name} {t('tui_not_found')}")
    console.print()


def _md_website(result: AnalysisResult, console: Console) -> None:
    site = result.website
    console.print(f"{t('md_section_website')}\n")
    if site.url:
        console.print(f"- {site.url}")
        if site.final_url and site.final_url != site.url:
            console.print(f"  → {site.final_url}")
    console.print(f"- {t('md_status', status=t(f'status_{site.status.value}'))}")
    console.print(f"- {site.interpretation}\n")


def _md_security(result: AnalysisResult, console: Console) -> None:
    security = result.security
    console.print(f"{t('md_section_security')}\n")
    console.print(f"- {t('md_status', status=t(f'status_{security.status.value}'))}")
    for update in security.updates:
        console.print(f"- #{update.number} {update.title}")
    console.print(f"- {security.interpretation}\n")


def _md_qualitative(result: AnalysisResult, console: Console) -> None:
    q = result.qualitative
    if not q.available:
        return
    console.print(f"{t('md_section_qualitative')}\n")
    if q.text_maintenance_state:
        console.print(
            f"- {t('md_text_state', state=t(f'state_{q.text_maintenance_state}'))}"
        )
    if q.roadmap:
        console.print(f"- {t('md_roadmap', text=q.roadmap)}")
    if q.commercial_support:
        console.print(f"- {t('md_commercial', text=q.commercial_support)}")
    if q.security_policy:
        console.print(f"- {t('md_security', text=q.security_policy)}")
    console.print(f"- {t('md_status', status=t(f'status_{q.status.value}'))}\n")


# ---------------------------------------------------------------------------
# Comparison rendering (JSON / Markdown)
# ---------------------------------------------------------------------------


def _warn_comparison_stderr(comparison: ComparisonResult) -> None:
    """Emit comparison + per-project warnings on stderr, deduplicated."""
    warnings: list[str] = list(comparison.warnings)
    for result in comparison.projects:
        warnings.extend(result.warnings)
    for warning in dict.fromkeys(warnings):
        print(f"warning: {warning}", file=sys.stderr)


def _md_commit_cell(days: int | None) -> str:
    """Compact last-commit cell for the comparison table."""
    if days is None:
        return "—"
    if days == 0:
        return t("cmp_table_today")
    return f"{days}d"


def _md_release_cell(result) -> str:
    """Compact latest-release cell: 'v2.4.1 · 12d' or '—'."""
    rh = result.release_health
    if not rh.latest_version:
        return "—"
    if rh.age_days is None:
        return rh.latest_version
    return f"{rh.latest_version} · {rh.age_days}d"


def _pair_md_label(pair) -> str:
    """'owner/repo vs owner/repo' Markdown label for a pair."""
    return (
        f"{pair.url_a.owner}/{pair.url_a.repo} vs {pair.url_b.owner}/{pair.url_b.repo}"
    )


def _render_comparison_markdown(comparison: ComparisonResult, console: Console) -> None:
    """Render the comparison as Markdown: comparability section, comparison
    table, then the full per-project reports."""
    _warn_comparison_stderr(comparison)
    console.print("# GitHub Health Comparison\n")

    console.print(f"{t('md_section_comparability')}\n")
    for pair in comparison.pairs:
        glyph = "✅" if pair.verdict == ComparisonVerdict.OK else "⚠️"
        console.print(f"- {glyph} **{_pair_md_label(pair)}**")
        if pair.similarity is not None:
            console.print(f"  - {t('cmp_similarity', score=pair.similarity)}")
        for reason in pair.reasons:
            console.print(f"  - {reason}")
        for note in pair.notes:
            console.print(f"  - _{note}_")
    console.print()
    console.print(f"_{t('cmp_legend')}_\n")

    console.print(f"## {t('cmp_pick_title')}\n")
    for rank, result in enumerate(ranked_projects(comparison), start=1):
        name = result.meta.full_name or f"{result.url.owner}/{result.url.repo}"
        glyph = _MD_GLYPHS.get(result.recommendation.level, "❓")
        message = result.recommendation.message
        line = f"{name} — {glyph} {message}" if message else f"{name} — {glyph}"
        marker = "★" if rank == 1 else f"{rank}"
        console.print(f"- **{marker} {line}**" if rank == 1 else f"- {marker}. {line}")
    console.print()

    console.print(f"{t('md_section_comparison_table')}\n")
    console.print(
        "| Project | Stars | License | Lang | State | Last commit | Bus | Downloads | Release | Verdict |"
    )
    console.print("|---|---|---|---|---|---|---|---|---|---|")
    for result in ranked_projects(comparison):
        meta = result.meta
        name = meta.full_name or f"{result.url.owner}/{result.url.repo}"
        stars = f"{meta.stars:,}"
        lic = result.license.spdx_id or "—"
        lang = result.languages.primary or "—"
        state = t(f"state_{result.maintenance.state.value}")
        commit = _md_commit_cell(result.maintenance.last_commit_days_ago)
        bus = str(result.contributors.bus_factor) if result.contributors.bus_factor else "—"
        downloads = (
            f"{project_downloads(result):,}" if project_downloads(result) else "—"
        )
        release = _md_release_cell(result)
        glyph = _MD_GLYPHS.get(result.recommendation.level, "❓")
        console.print(
            f"| {name} | {stars} | {lic} | {lang} | {state} | {commit} | {bus} | {downloads} | {release} | {glyph} |"
        )
    console.print()
    console.print(f"*{t('cmp_rank_note')}*\n")

    # Full per-project reports (same content as a single analysis).
    for result in comparison.projects:
        _render_markdown(result, console)


def _render_comparison_json(comparison: ComparisonResult, console: Console) -> None:
    """Render the comparison as JSON: full projects, pair verdicts, warnings."""
    _warn_comparison_stderr(comparison)
    payload = {
        "projects": [asdict(result) for result in comparison.projects],
        "ranking": [
            result.meta.full_name or f"{result.url.owner}/{result.url.repo}"
            for result in ranked_projects(comparison)
        ],
        "pairs": [
            {
                "url_a": str(pair.url_a),
                "url_b": str(pair.url_b),
                "kinds": [kind.value for kind in pair.kinds],
                "subject": pair.subject.value,
                "language_compatible": pair.language_compatible,
                "kind_mismatch": pair.kind_mismatch,
                "verdict": pair.verdict.value,
                "similarity": pair.similarity,
                "reasons": pair.reasons,
                "notes": pair.notes,
            }
            for pair in comparison.pairs
        ],
        "warnings": comparison.warnings,
    }
    console.print_json(json.dumps(payload, default=str))


_RENDERERS = {
    "tui": render_dashboard,
    "json": _render_json,
    "markdown": _render_markdown,
}

_COMPARISON_RENDERERS = {
    "tui": render_comparison,
    "json": _render_comparison_json,
    "markdown": _render_comparison_markdown,
}


# ---------------------------------------------------------------------------
# CLI group & commands
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

_ENV_VARS_HELP = textwrap.dedent(
    """\
    Environment variables:
      GITHUB_TOKEN                 GitHub API token (raises API rate limits)
      GH_SCORE_CONFIG              Path to the TOML config file
      GH_SCORE_CACHE_DIR           Cache directory
      GH_SCORE_CACHE_TTL_HOURS     Cache TTL in hours
      GH_SCORE_LLM_ENABLED         1/true/yes to enable the optional LLM analysis
      GH_SCORE_LLM_PROVIDER        LLM provider name (informational)
      GH_SCORE_LLM_BASE_URL        OpenAI-compatible base URL (e.g. https://api.openai.com/v1)
      GH_SCORE_LLM_MODEL           LLM model name
      GH_SCORE_LLM_API_KEY         LLM API key (empty for local servers such as Ollama)
      GH_SCORE_LLM_DISABLE_REASONING  1/true/yes to skip the reasoning pass (models that
                                  burn the token budget on chain-of-thought)
      LIBRARIES_IO_API_KEY         libraries.io API key (dependents for PyPI/npm/Maven)
    """
)


class _EpilogMixin:
    """Print the epilog verbatim.

    Click's default formatter collapses all whitespace (including newlines)
    via replace_whitespace, which would turn the env-var table into a wall
    of text. We write the preformatted block as-is instead.
    """

    # Declared here so static analyzers see it through the mixin; click
    # sets it on commands/groups that define an epilog.
    epilog: str | None = None

    def format_epilog(self, ctx, formatter):  # pylint: disable=unused-argument
        if self.epilog:
            formatter.write(f"\n{self.epilog}")


class _EnvHelpCommand(_EpilogMixin, click.Command):
    """Command whose epilog (env var docs) is printed verbatim."""


class DefaultGroup(_EpilogMixin, click.Group):
    """Click group that routes unrecognized tokens to a default command.

    When the first argument is a known subcommand (``config``, ``report``,
    ``analyze``), Click dispatches normally.  When it is something else
    (a URL, a path), it is forwarded as a positional argument to the
    default command (``analyze``).
    """

    def __init__(self, *args, default_command: str = "analyze", **kwargs):
        super().__init__(*args, invoke_without_command=True, **kwargs)
        self.default_command = default_command

    def resolve_command(self, ctx, args):
        if not args:
            return super().resolve_command(ctx, args)

        cmd_name = args[0]
        cmd = self.get_command(ctx, cmd_name)

        if cmd is not None:
            return cmd_name, cmd, args[1:]

        # Unknown token (URL, path, …) → route to the default command.
        default = self.commands.get(self.default_command)
        if default is not None:
            return self.default_command, default, args

        return super().resolve_command(ctx, args)


def _default_analyze() -> None:
    """Group callback: invoked on every ``gh-score`` call. Dispatches to
    the subcommand or the default ``analyze``."""
    ctx = click.get_current_context()
    if ctx.invoked_subcommand is not None:
        return  # a real subcommand will handle it
    ctx.invoke(analyze)


cli = DefaultGroup(
    default_command="analyze",
    name="gh-score",
    callback=_default_analyze,
    epilog=_ENV_VARS_HELP,
    help="GitHub Project Health Scorer.\n\nAnalyze a GitHub repository's health, maturity, and sustainability.",
)


async def _gather_analyses(
    targets: list[tuple[str, bool]], config: Config
) -> list[AnalysisResult]:
    """Analyze every target concurrently, each with its own local/remote
    mode (the gather lives inside asyncio.run, which cannot wrap
    asyncio.gather directly)."""
    return await asyncio.gather(
        *(
            analyze_repo_async(target, config, use_local=is_local)
            for target, is_local in targets
        )
    )


def _resolve_and_validate(
    url: str | None, remote: bool, local: bool, console: Console
) -> tuple[str, bool]:
    """Resolve a target (URL/path/CWD) to (target, is_local), validating it.

    Exits with the localized usage message when no target can be found.
    """
    resolved, is_local = _resolve_target(url, force_remote=remote)
    if resolved is None:
        console.print(f"[red]{t('cli_error')}[/red] {t('cli_no_target')}")
        console.print(t("cli_usage_1"))
        console.print(t("cli_usage_2"))
        sys.exit(1)
    if local:
        is_local = True
    _validate_url(resolved, is_local, console)
    return resolved, is_local


@cli.command(name="analyze", epilog=_ENV_VARS_HELP, cls=_EnvHelpCommand)
@click.argument("urls", nargs=-1, required=False)
@click.option("--local", is_flag=True, help="Force local analysis")
@click.option(
    "--remote", is_flag=True, help="Force remote API analysis even when inside a clone"
)
@click.option("--refresh", is_flag=True, help="Bypass cache")
@click.option("--no-llm", is_flag=True, help="Disable LLM analysis")
@click.option("--config", "config_path", help="Path to config file")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["tui", "json", "markdown"]),
    default="tui",
)
# pylint: disable=too-many-arguments,too-many-positional-arguments
def analyze(
    urls: tuple[str, ...],
    local: bool,
    remote: bool,
    refresh: bool,
    no_llm: bool,
    config_path: str | None,
    output_format: str,
) -> None:
    """Analyze a repository, or compare several repositories (2+ URLs)."""
    console = Console()

    # Resolve every target; with no argument, fall back to the current
    # directory when it is a git clone.
    if urls:
        targets = [_resolve_and_validate(u, remote, local, console) for u in urls]
    else:
        targets = [_resolve_and_validate(None, remote, local, console)]

    config = _prepare_config(config_path, refresh, no_llm, console)

    try:
        if len(targets) == 1:
            # Single repository: existing dashboard behavior.
            target, is_local = targets[0]
            with console.status(f"[bold blue]{t('cli_analyzing')}[/bold blue]"):
                result = analyze_repo(target, config, use_local=is_local)

            renderer = _RENDERERS.get(output_format, render_dashboard)
            renderer(result, console)
        else:
            # Comparison mode: analyze in parallel, assess comparability.
            with console.status(f"[bold blue]{t('cli_analyzing_many')}[/bold blue]"):
                results = asyncio.run(_gather_analyses(targets, config))
            comparison = compare_results(list(results))
            # Optional LLM: lift deterministic 'unknown' subject verdicts.
            if config.llm.enabled:
                asyncio.run(refine_comparison_subjects(comparison, config))

            renderer = _COMPARISON_RENDERERS.get(output_format, render_comparison)
            renderer(comparison, console)

    except Exception as exc:
        console.print(f"[red]{t('cli_error')}[/red] {exc}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            console.print_exception()
        sys.exit(1)


@cli.command()
@click.argument("urls", nargs=-1, required=False)
def report(urls: tuple[str, ...]) -> None:
    """Generate a detailed report (same as analyze)."""
    ctx = click.get_current_context()
    ctx.invoke(analyze, urls=urls)


@cli.command()
def config() -> None:
    """Show current configuration."""
    cfg = Config.load()
    console = Console()

    table = Table(title=t("cli_config_title"))
    table.add_column(t("cli_config_setting"), style="cyan")
    table.add_column(t("cli_config_value"), style="green")

    table.add_row(
        t("cli_cfg_token"),
        t("cli_cfg_set") if cfg.github.token else t("cli_cfg_not_set"),
    )
    table.add_row(t("cli_cfg_cache_dir"), cfg.cache.dir)
    table.add_row(t("cli_cfg_cache_ttl"), f"{cfg.cache.ttl_hours}h")
    table.add_row(t("cli_cfg_llm_enabled"), str(cfg.llm.enabled))
    table.add_row(t("cli_cfg_llm_provider"), cfg.llm.provider)
    table.add_row(t("cli_cfg_llm_model"), cfg.llm.model)
    table.add_row(t("cli_cfg_llm_base_url"), cfg.llm.base_url)
    table.add_row(t("cli_cfg_llm_disable_reasoning"), str(cfg.llm.disable_reasoning))
    libraries_io = cfg.registries.libraries_io_api_key
    table.add_row(
        t("cli_cfg_libraries_io"),
        (t("cli_cfg_set") + " " + libraries_io[-4:]) if libraries_io else t("cli_cfg_not_set"),
    )

    console.print(table)


if __name__ == "__main__":
    cli()
