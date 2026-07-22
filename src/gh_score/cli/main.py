"""CLI entry point for gh-score."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from gh_score.config import Config
from gh_score.core.api import analyze_repo
from gh_score.core.models import RepoUrl
from gh_score.cli.tui import render_dashboard


@click.group(invoke_without_command=True)
@click.argument("url_or_path", required=False)
@click.option("--local", is_flag=True, help="Force local analysis")
@click.option("--refresh", is_flag=True, help="Bypass cache")
@click.option("--no-llm", is_flag=True, help="Disable LLM analysis")
@click.option("--config", "config_path", help="Path to config file")
@click.option("--format", "output_format", type=click.Choice(["tui", "json", "markdown"]), default="tui")
@click.pass_context
def cli(ctx: click.Context, url_or_path: str | None, local: bool, refresh: bool,
        no_llm: bool, config_path: str | None, output_format: str) -> None:
    """GitHub Project Health Scorer.

    Analyze a GitHub repository's health, maturity, and sustainability.

    If no URL is provided, analyzes the current directory if it's a git clone.
    """
    # Check if url_or_path is actually a subcommand
    if url_or_path in ("config", "report"):
        # Invoke the subcommand instead
        cmd = ctx.command.commands.get(url_or_path)
        if cmd:
            ctx.invoke(cmd)
            return
    
    if ctx.invoked_subcommand is not None:
        return

    console = Console()

    # Determine what to analyze
    if url_or_path is None:
        # Try current directory
        cwd = Path.cwd()
        if (cwd / ".git").exists():
            url_or_path = str(cwd)
            local = True
        else:
            console.print("[red]Error:[/red] No URL provided and current directory is not a git repository.")
            console.print("Usage: gh-score [URL] [OPTIONS]")
            console.print("       gh-score https://github.com/owner/repo")
            sys.exit(1)

    # Validate URL if not local
    if not local and not Path(url_or_path).exists():
        try:
            RepoUrl.parse(url_or_path)
        except ValueError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    # Load config
    config = Config.load(config_path)

    if refresh:
        # Clear cache
        from gh_score.core.cache import Cache
        cache = Cache(config.cache.dir)
        cache.clear()
        console.print("[dim]Cache cleared[/dim]")

    if no_llm:
        config.llm.enabled = False

    # Run analysis
    try:
        with console.status("[bold blue]Analyzing repository...[/bold blue]"):
            result = analyze_repo(url_or_path, config, use_local=local)

        # Render output
        if output_format == "tui":
            render_dashboard(result, console)
        elif output_format == "json":
            import json
            from dataclasses import asdict
            console.print_json(json.dumps(asdict(result), default=str))
        elif output_format == "markdown":
            _render_markdown(result, console)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            console.print_exception()
        sys.exit(1)


def _render_markdown(result, console: Console) -> None:
    """Render results as Markdown."""
    console.print(f"# GitHub Health Report: {result.url}\n")

    if result.meta.description:
        console.print(f"_{result.meta.description}_\n")

    console.print(f"**Stars:** {result.meta.stars:,} | **Forks:** {result.meta.forks:,}\n")

    console.print("## Release Health\n")
    rh = result.release_health
    if rh.latest_version:
        console.print(f"- **Latest:** {rh.latest_version}")
    if rh.age_days is not None:
        console.print(f"- **Age:** {rh.age_days} days")
    if rh.cadence_days is not None:
        console.print(f"- **Cadence:** {rh.cadence_days:.0f} days/release")
    console.print(f"- **Status:** {rh.status.value}\n")

    console.print("## License\n")
    lic = result.license
    if lic.spdx_id:
        console.print(f"- **License:** {lic.spdx_id} ({lic.family.value})")
    else:
        console.print("- **License:** Not detected")
    console.print(f"- **Status:** {lic.status.value}\n")

    console.print("## Contributors\n")
    contrib = result.contributors
    console.print(f"- **Total authors:** {contrib.total_authors}")
    console.print(f"- **Bus factor:** {contrib.bus_factor}")
    if contrib.bot_ratio > 0:
        console.print(f"- **Bot ratio:** {contrib.bot_ratio:.0%}")
    if contrib.lead:
        console.print(f"- **Lead:** {contrib.lead.login}")
    console.print(f"- **Status:** {contrib.status.value}\n")

    console.print("## Maintenance\n")
    maint = result.maintenance
    console.print(f"- **State:** {maint.state.value}")
    if maint.last_commit_days_ago is not None:
        console.print(f"- **Last commit:** {maint.last_commit_days_ago} days ago")
    if maint.commits_per_month is not None:
        console.print(f"- **Frequency:** {maint.commits_per_month:.1f} commits/month")
    console.print(f"- **Status:** {maint.status.value}\n")

    console.print("## Languages\n")
    lang = result.languages
    if lang.primary:
        console.print(f"- **Primary:** {lang.primary}")
    if lang.breakdown:
        console.print("- **Breakdown:**")
        sorted_langs = sorted(lang.breakdown.items(), key=lambda x: x[1], reverse=True)[:5]
        for lang_name, pct in sorted_langs:
            console.print(f"  - {lang_name}: {pct:.1f}%\n")

    console.print("## Sustainability\n")
    sust = result.sustainability
    if sust.foundation:
        console.print(f"- **Foundation:** {sust.foundation}")
    if sust.funding_platforms:
        console.print(f"- **Funding:** {', '.join(sust.funding_platforms)}")
    if sust.corporate_backing:
        console.print(f"- **Corporate backing:** {sust.corporate_backing}")
    console.print(f"- **Status:** {sust.status.value}\n")


@cli.command()
@click.argument("url", required=False)
def report(url: str | None) -> None:
    """Generate a detailed report (alias for main command with format options)."""
    # Just invoke the main CLI
    ctx = click.get_current_context()
    ctx.invoke(cli, url_or_path=url)


@cli.command()
def config() -> None:
    """Show current configuration."""
    from rich.table import Table

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
