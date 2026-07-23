"""CLI entry point for gh-score."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from gh_score.config import Config
from gh_score.core.api import analyze_repo
from gh_score.core.cache import Cache
from gh_score.core.models import AnalysisResult, RepoUrl
from gh_score.cli.tui import render_dashboard

if TYPE_CHECKING:
    from collections.abc import Sequence


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
        console.print(f"[red]Error:[/red] {exc}")
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
        console.print("[dim]Cache cleared[/dim]")

    if no_llm:
        config.llm.enabled = False

    return config


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_json(result: AnalysisResult, console: Console) -> None:
    """Render results as JSON."""
    console.print_json(json.dumps(asdict(result), default=str))


def _render_markdown(result: AnalysisResult, console: Console) -> None:
    """Render results as Markdown."""
    console.print(f"# GitHub Health Report: {result.url}\n")

    if result.meta.description:
        console.print(f"_{result.meta.description}_\n")

    console.print(f"**Stars:** {result.meta.stars:,} | **Forks:** {result.meta.forks:,}\n")

    _md_release_health(result, console)
    _md_license(result, console)
    _md_contributors(result, console)
    _md_maintenance(result, console)
    _md_languages(result, console)
    _md_sustainability(result, console)


def _md_release_health(result: AnalysisResult, console: Console) -> None:
    rh = result.release_health
    console.print("## Release Health\n")
    if rh.latest_version:
        console.print(f"- **Latest:** {rh.latest_version}")
    if rh.age_days is not None:
        console.print(f"- **Age:** {rh.age_days} days")
    if rh.cadence_days is not None:
        console.print(f"- **Cadence:** {rh.cadence_days:.0f} days/release")
    console.print(f"- **Status:** {rh.status.value}\n")


def _md_license(result: AnalysisResult, console: Console) -> None:
    lic = result.license
    console.print("## License\n")
    if lic.spdx_id:
        console.print(f"- **License:** {lic.spdx_id} ({lic.family.value})")
    else:
        console.print("- **License:** Not detected")
    console.print(f"- **Status:** {lic.status.value}\n")


def _md_contributors(result: AnalysisResult, console: Console) -> None:
    contrib = result.contributors
    console.print("## Contributors\n")
    console.print(f"- **Total authors:** {contrib.total_authors}")
    console.print(f"- **Bus factor:** {contrib.bus_factor}")
    if contrib.bot_ratio > 0:
        console.print(f"- **Bot ratio:** {contrib.bot_ratio:.0%}")
    if contrib.lead:
        console.print(f"- **Lead:** {contrib.lead.login}")
    console.print(f"- **Status:** {contrib.status.value}\n")


def _md_maintenance(result: AnalysisResult, console: Console) -> None:
    maint = result.maintenance
    console.print("## Maintenance\n")
    console.print(f"- **State:** {maint.state.value}")
    if maint.last_commit_days_ago is not None:
        console.print(f"- **Last commit:** {maint.last_commit_days_ago} days ago")
    if maint.commits_per_month is not None:
        console.print(f"- **Frequency:** {maint.commits_per_month:.1f} commits/month")
    console.print(f"- **Status:** {maint.status.value}\n")


def _md_languages(result: AnalysisResult, console: Console) -> None:
    lang = result.languages
    console.print("## Languages\n")
    if lang.primary:
        console.print(f"- **Primary:** {lang.primary}")
    if lang.breakdown:
        console.print("- **Breakdown:**")
        sorted_langs = sorted(lang.breakdown.items(), key=lambda x: x[1], reverse=True)[:5]
        for lang_name, pct in sorted_langs:
            console.print(f"  - {lang_name}: {pct:.1f}%\n")


def _md_sustainability(result: AnalysisResult, console: Console) -> None:
    sust = result.sustainability
    console.print("## Sustainability\n")
    if sust.foundation:
        console.print(f"- **Foundation:** {sust.foundation}")
    if sust.funding_platforms:
        console.print(f"- **Funding:** {', '.join(sust.funding_platforms)}")
    if sust.corporate_backing:
        console.print(f"- **Corporate backing:** {sust.corporate_backing}")
    console.print(f"- **Status:** {sust.status.value}\n")


_RENDERERS = {
    "tui": render_dashboard,
    "json": _render_json,
    "markdown": _render_markdown,
}


# ---------------------------------------------------------------------------
# CLI group & commands
# ---------------------------------------------------------------------------


class DefaultGroup(click.Group):
    """Click group that falls back to a default command."""

    def __init__(self, *args, default_command: str = "analyze", **kwargs):
        super().__init__(*args, **kwargs)
        self.default_command = default_command

    def resolve_command(
        self, ctx: click.Context, args: Sequence[str]
    ) -> tuple[str | None, click.Command, Sequence[str]]:
        # If the first arg is a known command, use it normally.
        if args and args[0] in self.commands:
            return super().resolve_command(ctx, args)

        # Otherwise inject the default command name so Click routes to it.
        return super().resolve_command(ctx, [self.default_command, *args])


cli = DefaultGroup(
    default_command="analyze",
    name="gh-score",
    help="GitHub Project Health Scorer.\n\nAnalyze a GitHub repository's health, maturity, and sustainability.",
)


@cli.command(name="analyze")
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
@click.pass_context
def analyze(
    ctx: click.Context,
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
        console.print(
            "[red]Error:[/red] No URL provided and current directory is not a git repository."
        )
        console.print("Usage: gh-score analyze [URL] [OPTIONS]")
        console.print("       gh-score https://github.com/owner/repo")
        sys.exit(1)

    # If user forced local mode, override
    if local:
        is_local = True

    _validate_url(resolved, is_local, console)
    config = _prepare_config(config_path, refresh, no_llm, console)

    try:
        with console.status("[bold blue]Analyzing repository...[/bold blue]"):
            result = analyze_repo(resolved, config, use_local=is_local)

        renderer = _RENDERERS.get(output_format, render_dashboard)
        renderer(result, console)

    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
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

    table = Table(title="Current Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("GitHub Token", "set" if cfg.github.token else "not set")
    table.add_row("Cache Dir", cfg.cache.dir)
    table.add_row("Cache TTL", f"{cfg.cache.ttl_hours}h")
    table.add_row("LLM Enabled", str(cfg.llm.enabled))
    table.add_row("LLM Provider", cfg.llm.provider)
    table.add_row("LLM Model", cfg.llm.model)
    table.add_row("LLM Base URL", cfg.llm.base_url)

    console.print(table)


if __name__ == "__main__":
    cli()
