"""TUI dashboard renderer using Rich.

Displays the analysis results as a terminal dashboard.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gh_score.core.models import (
    AnalysisResult,
    MaintenanceState,
    Status,
)


def _status_glyph(status: Status) -> tuple[str, str]:
    """Return (glyph, color) for a status."""
    if status == Status.HEALTHY:
        return "✓", "green"
    if status == Status.WARNING:
        return "⚠", "yellow"
    if status == Status.CRITICAL:
        return "✗", "red"
    return "?", "dim"


def _render_release_health(result: AnalysisResult) -> Panel:
    """Render release health panel."""
    rh = result.release_health
    glyph, color = _status_glyph(rh.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if rh.latest_version:
        content.append(f"latest: {rh.latest_version}", style="bold")
        if rh.latest_date:
            content.append(f" ({rh.latest_date.strftime('%Y-%m-%d')})")
        content.append("\n")

    if rh.age_days is not None:
        content.append(f"age: {rh.age_days} days\n")

    if rh.cadence_days is not None:
        content.append(f"cadence: {rh.cadence_days:.0f} days/release\n")

    if rh.semver_compliant is not None:
        semver_str = "yes" if rh.semver_compliant else "no"
        content.append(f"semver: {semver_str}\n")

    if rh.is_prerelease:
        content.append("status: pre-release\n", style="yellow")

    if rh.interpretation:
        content.append(f"\n{rh.interpretation}", style="dim")

    return Panel(content, title="Release Health", border_style=color)


def _render_license(result: AnalysisResult) -> Panel:
    """Render license panel."""
    lic = result.license
    glyph, color = _status_glyph(lic.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if lic.spdx_id:
        content.append(lic.spdx_id, style="bold")
        content.append(f" ({lic.family.value}")
        if lic.osi_approved:
            content.append(", OSI")
        content.append(")\n")
    else:
        content.append("No license detected\n", style="red")

    if lic.interpretation:
        content.append(f"\n{lic.interpretation}", style="dim")

    return Panel(content, title="License", border_style=color)


def _render_contributors(result: AnalysisResult) -> Panel:
    """Render contributors panel."""
    contrib = result.contributors
    glyph, color = _status_glyph(contrib.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    content.append(f"total: {contrib.total_authors}\n")
    content.append(f"bus factor: {contrib.bus_factor}\n")

    if contrib.bot_ratio > 0:
        content.append(f"bots: {contrib.bot_ratio:.0%}\n")

    if contrib.lead:
        content.append(f"lead: {contrib.lead.login}")
        if contrib.lead.commits > 0:
            content.append(f" ({contrib.lead.commits} commits)")
        content.append("\n")

    if contrib.historical_lead:
        content.append(f"historical: {contrib.historical_lead.login}\n")

    if contrib.minor_count > 0:
        content.append(f"minor: {contrib.minor_count}\n")

    # Activity trend
    if contrib.activity_trend:
        commits_3m = contrib.activity_trend.get("3m", 0)
        commits_12m = contrib.activity_trend.get("12m", 0)
        if commits_3m > 0:
            content.append(f"activity: {commits_3m} commits (3m)\n")
        elif commits_12m > 0:
            content.append(f"activity: {commits_12m} commits (12m)\n")

    if contrib.interpretation:
        content.append(f"\n{contrib.interpretation}", style="dim")

    return Panel(content, title="Contributors", border_style=color)


def _render_maintenance(result: AnalysisResult) -> Panel:
    """Render maintenance panel."""
    maint = result.maintenance
    glyph, color = _status_glyph(maint.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    # State
    state_colors = {
        MaintenanceState.ACTIVE: "green",
        MaintenanceState.MAINTENANCE: "yellow",
        MaintenanceState.ABANDONED: "red",
        MaintenanceState.UNKNOWN: "dim",
    }
    state_color = state_colors.get(maint.state, "dim")
    content.append(f"state: {maint.state.value}\n", style=state_color)

    if maint.last_commit_days_ago is not None:
        content.append(f"last commit: {maint.last_commit_days_ago}d ago\n")

    if maint.commits_per_month is not None:
        content.append(f"frequency: {maint.commits_per_month:.1f} commits/month\n")

    if maint.issue_velocity_days is not None:
        content.append(f"issues closed: {maint.issue_velocity_days:.0f}d\n")

    if maint.stale_issue_ratio is not None:
        content.append(f"stale issues: {maint.stale_issue_ratio:.0%}\n")

    if maint.interpretation:
        content.append(f"\n{maint.interpretation}", style="dim")

    return Panel(content, title="Maintenance", border_style=color)


def _render_languages(result: AnalysisResult) -> Panel:
    """Render languages panel."""
    lang = result.languages

    content = Text()

    if lang.primary:
        content.append(f"primary: {lang.primary}\n", style="bold")

    # Top languages
    if lang.breakdown:
        sorted_langs = sorted(
            lang.breakdown.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]

        for lang_name, pct in sorted_langs:
            bar_len = int(pct / 5)  # Scale to 20 chars max
            progress_bar = "█" * bar_len
            content.append(f"{lang_name:12} {pct:5.1f}% {progress_bar}\n")

    if lang.ecosystem:
        content.append(f"\necosystem: {lang.ecosystem}\n", style="dim")

    if lang.interpretation:
        content.append(f"\n{lang.interpretation}", style="dim")

    return Panel(content, title="Languages", border_style="blue")


def _render_sustainability(result: AnalysisResult) -> Panel:
    """Render sustainability panel."""
    sust = result.sustainability
    glyph, color = _status_glyph(sust.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if sust.foundation:
        content.append(f"foundation: {sust.foundation}\n", style="green")

    if sust.funding_platforms:
        content.append(f"funding: {', '.join(sust.funding_platforms)}\n")

    if sust.corporate_backing:
        content.append(f"corporate: {sust.corporate_backing}\n")

    if sust.governance_model:
        content.append(f"governance: {sust.governance_model}\n")

    # LLM-extracted signals
    if sust.llm_signals:
        sponsors = sust.llm_signals.get("sponsors")
        if sponsors and isinstance(sponsors, list):
            content.append(f"LLM sponsors: {', '.join(sponsors)}\n", style="dim")
        roadmap = sust.llm_signals.get("roadmap")
        if roadmap and isinstance(roadmap, str):
            content.append(f"LLM roadmap: {roadmap[:80]}...\n", style="dim")
        security = sust.llm_signals.get("security_policy")
        if security and isinstance(security, str):
            content.append(f"LLM security: {security[:80]}...\n", style="dim")
        commercial = sust.llm_signals.get("commercial_support")
        if commercial and isinstance(commercial, str):
            content.append(f"LLM commercial: {commercial[:80]}...\n", style="dim")

    if not any([sust.foundation, sust.funding_platforms, sust.corporate_backing]):
        content.append("no backing detected\n", style="yellow")

    if sust.interpretation:
        content.append(f"\n{sust.interpretation}", style="dim")

    return Panel(content, title="Sustainability", border_style=color)


def _render_registries(result: AnalysisResult) -> Panel | None:
    """Render package registries panel if any."""
    if not result.registries:
        return None

    content = Text()

    for reg in result.registries:
        if reg.exists:
            content.append(f"{reg.ecosystem}: ", style="bold")
            content.append(f"✓ {reg.package_name}")
            if reg.latest_version:
                content.append(f" @ {reg.latest_version}")
            content.append("\n")

            # Download stats
            if reg.downloads is not None:
                content.append(f"  downloads: {reg.downloads:,}\n", style="dim")
            if reg.recent_downloads is not None:
                content.append(f"  recent: {reg.recent_downloads:,}\n", style="dim")

            # License info
            if reg.registry_license:
                content.append(f"  license: {reg.registry_license}\n", style="dim")
                if reg.license_matches_github is not None:
                    match_str = "matches" if reg.license_matches_github else "differs"
                    content.append(f"  GitHub license: {match_str}\n", style="dim")

            # Deprecated flag
            if reg.deprecated:
                content.append("  ⚠ deprecated\n", style="yellow")

            # Heuristic warning
            if reg.is_heuristic:
                content.append("  (name inferred from repo)\n", style="dim")
        else:
            content.append(f"{reg.ecosystem}: ", style="bold")
            content.append(f"✗ {reg.package_name} (not found)\n", style="dim")

    return Panel(content, title="Package Registries", border_style="blue")


def render_dashboard(result: AnalysisResult, console: Console | None = None) -> None:
    """Render the full TUI dashboard.

    Args:
        result: Analysis result to render
        console: Rich console (creates one if None)
    """
    if console is None:
        console = Console()

    # Header
    console.print()
    console.print(f"[bold]GitHub Health Dashboard[/bold] - {result.url}", style="blue")
    console.print(f"[dim]{result.meta.description or 'No description'}[/dim]")
    console.print()

    # Summary table
    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_row(f"[dim]Stars:[/dim] {result.meta.stars:,}")
    summary.add_row(f"[dim]Forks:[/dim] {result.meta.forks:,}")
    summary.add_row(f"[dim]Created:[/dim] {result.meta.created_at.strftime('%Y-%m-%d') if result.meta.created_at else 'N/A'}")
    console.print(summary)
    console.print()

    # Main panels (2x2 grid)
    grid = Table.grid(padding=(1, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    grid.add_row(
        _render_release_health(result),
        _render_license(result),
    )
    grid.add_row(
        _render_contributors(result),
        _render_maintenance(result),
    )

    console.print(grid)

    # Secondary panels
    grid2 = Table.grid(padding=(1, 1))
    grid2.add_column(ratio=1)
    grid2.add_column(ratio=1)

    grid2.add_row(
        _render_languages(result),
        _render_sustainability(result),
    )

    console.print(grid2)

    # Registries if any
    registries_panel = _render_registries(result)
    if registries_panel:
        console.print(registries_panel)

    console.print()
