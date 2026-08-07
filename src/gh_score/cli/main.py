"""CLI entry point for gh-score."""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from gh_score.config import Config
from gh_score.core.api import analyze_repo
from gh_score.core.analyzers.license_analyzer import license_family_label
from gh_score.core.cache import Cache
from gh_score.core.models import AnalysisResult, RecommendationLevel, RepoUrl
from gh_score.i18n import t
from gh_score.cli.tui import render_dashboard


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
    _md_website(result, console)
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


def _md_website(result: AnalysisResult, console: Console) -> None:
    site = result.website
    console.print(f"{t('md_section_website')}\n")
    if site.url:
        console.print(f"- {site.url}")
        if site.final_url and site.final_url != site.url:
            console.print(f"  → {site.final_url}")
    console.print(f"- {t('md_status', status=t(f'status_{site.status.value}'))}")
    console.print(f"- {site.interpretation}\n")


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


_RENDERERS = {
    "tui": render_dashboard,
    "json": _render_json,
    "markdown": _render_markdown,
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
    """Group callback: invoked when ``gh-score`` is called without a
    subcommand (``invoke_without_command=True``)."""
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


@cli.command(name="analyze", epilog=_ENV_VARS_HELP, cls=_EnvHelpCommand)
@click.argument("url_or_path", required=False)
@click.option("--local", is_flag=True, help="Force local analysis")
@click.option("--remote", is_flag=True, help="Force remote API analysis even when inside a clone")
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
    url_or_path: str | None,
    local: bool,
    remote: bool,
    refresh: bool,
    no_llm: bool,
    config_path: str | None,
    output_format: str,
) -> None:
    """Analyze a repository (default command)."""
    console = Console()

    resolved, is_local = _resolve_target(url_or_path, force_remote=remote)
    if resolved is None:
        console.print(f"[red]{t('cli_error')}[/red] {t('cli_no_target')}")
        console.print(t("cli_usage_1"))
        console.print(t("cli_usage_2"))
        sys.exit(1)

    # If user forced local mode, override
    if local:
        is_local = True

    _validate_url(resolved, is_local, console)
    config = _prepare_config(config_path, refresh, no_llm, console)

    try:
        with console.status(f"[bold blue]{t('cli_analyzing')}[/bold blue]"):
            result = analyze_repo(resolved, config, use_local=is_local)

        renderer = _RENDERERS.get(output_format, render_dashboard)
        renderer(result, console)

    except Exception as exc:
        console.print(f"[red]{t('cli_error')}[/red] {exc}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            console.print_exception()
        sys.exit(1)


@cli.command()
@click.argument("url", required=False)
def report(url: str | None) -> None:
    """Generate a detailed report (same as analyze)."""
    ctx = click.get_current_context()
    ctx.invoke(analyze, url_or_path=url)


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

    console.print(table)


if __name__ == "__main__":
    cli()
