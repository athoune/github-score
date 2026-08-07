"""TUI dashboard renderer using Rich.

Displays the analysis results as a terminal dashboard.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gh_score.core.analyzers.license_analyzer import license_family_label
from gh_score.core.models import (
    AnalysisResult,
    MaintenanceState,
    RecommendationLevel,
    Status,
)
from gh_score.i18n import t


# Traffic-light glyphs and colors for the recommendation verdict.
_TRAFFIC_LIGHT = {
    RecommendationLevel.GREEN: ("🟢", "green"),
    RecommendationLevel.ORANGE: ("🟠", "yellow"),
    RecommendationLevel.RED: ("🔴", "red"),
}


def _status_glyph(status: Status) -> tuple[str, str]:
    """Return (glyph, color) for a status."""
    if status == Status.HEALTHY:
        return "✓", "green"
    if status == Status.WARNING:
        return "⚠", "yellow"
    if status == Status.CRITICAL:
        return "✗", "red"
    return "?", "dim"


def _render_warnings(result: AnalysisResult) -> Panel | None:
    """Render the localized warnings panel (missing token, LLM issues)."""
    if not result.warnings:
        return None

    content = Text()
    for warning in result.warnings:
        content.append(f"⚠ {warning}\n", style="yellow")

    return Panel(content, title=t("panel_warnings"), border_style="yellow")


def _render_recommendation(result: AnalysisResult) -> Panel:
    """Render the traffic-light recommendation panel."""
    rec = result.recommendation
    glyph, color = _TRAFFIC_LIGHT.get(rec.level, ("❓", "dim"))

    content = Text()
    content.append(f"{glyph} ", style=color)
    content.append(rec.message, style=color)

    if rec.reasoning:
        content.append("\n")
        for reason in rec.reasoning:
            content.append(f"  • {reason}\n", style="dim")

    if rec.confidence > 0:
        content.append(f"\n{t('ui_confidence', conf=rec.confidence)}", style="dim")

    return Panel(content, title="Recommendation", border_style=color)


def _render_llm_recommendation(result: AnalysisResult) -> Panel | None:
    """Render the optional LLM refined recommendation panel."""
    rec = result.llm_recommendation
    if not rec or not rec.level:
        return None

    try:
        level = RecommendationLevel(rec.level)
    except ValueError:
        return None

    glyph, color = _TRAFFIC_LIGHT.get(level, ("❓", "dim"))

    content = Text()
    content.append(f"{glyph} ", style=color)
    content.append(rec.message, style=color)

    if rec.explanation:
        content.append(f"\n{rec.explanation}", style="dim")

    if rec.confidence > 0:
        content.append(f"\n{t('ui_confidence', conf=rec.confidence)}", style="dim")

    return Panel(content, title=t("panel_llm_recommendation"), border_style=color)


def _render_release_health(result: AnalysisResult) -> Panel:
    """Render release health panel."""
    rh = result.release_health
    glyph, color = _status_glyph(rh.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if rh.latest_version:
        content.append(t("tui_latest", version=rh.latest_version), style="bold")
        if rh.latest_date:
            content.append(f" ({rh.latest_date.strftime('%Y-%m-%d')})")
        content.append("\n")

    if rh.age_days is not None:
        content.append(f"{t('tui_age', days=rh.age_days)}\n")

    if rh.cadence_days is not None:
        content.append(f"{t('tui_cadence', days=rh.cadence_days)}\n")

    if rh.semver_compliant is not None:
        key = "tui_semver_yes" if rh.semver_compliant else "tui_semver_no"
        content.append(f"{t(key)}\n")

    if rh.is_prerelease:
        content.append(f"{t('tui_prerelease')}\n", style="yellow")

    if rh.interpretation:
        content.append(f"\n{rh.interpretation}", style="dim")

    return Panel(content, title=t("panel_release_health"), border_style=color)


def _render_license(result: AnalysisResult) -> Panel:
    """Render license panel."""
    lic = result.license
    glyph, color = _status_glyph(lic.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if lic.spdx_id:
        content.append(lic.spdx_id, style="bold")
        content.append(f" ({license_family_label(lic.family)}")
        if lic.osi_approved:
            content.append(", OSI")
        content.append(")\n")
    else:
        content.append(f"{t('tui_no_license')}\n", style="red")

    if lic.interpretation:
        content.append(f"\n{lic.interpretation}", style="dim")

    return Panel(content, title=t("panel_license"), border_style=color)


def _render_contributors(result: AnalysisResult) -> Panel:
    """Render contributors panel."""
    contrib = result.contributors
    glyph, color = _status_glyph(contrib.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    content.append(f"{t('tui_total', count=contrib.total_authors)}\n")
    content.append(f"{t('tui_bus_factor', count=contrib.bus_factor)}\n")

    if contrib.bot_ratio > 0:
        content.append(f"{t('tui_bots', ratio=contrib.bot_ratio)}\n")

    if contrib.lead:
        content.append(t("tui_lead", login=contrib.lead.login))
        if contrib.lead.commits > 0:
            content.append(f" ({contrib.lead.commits} commits)")
        content.append("\n")

    if contrib.historical_lead:
        content.append(
            f"{t('tui_historical', login=contrib.historical_lead.login)}\n"
        )

    if contrib.minor_count > 0:
        content.append(f"{t('tui_minor', count=contrib.minor_count)}\n")

    # Activity trend
    if contrib.activity_trend:
        commits_3m = contrib.activity_trend.get("3m", 0)
        commits_12m = contrib.activity_trend.get("12m", 0)
        if commits_3m > 0:
            content.append(f"{t('tui_activity_3m', count=commits_3m)}\n")
        elif commits_12m > 0:
            content.append(f"{t('tui_activity_12m', count=commits_12m)}\n")

    if contrib.interpretation:
        content.append(f"\n{contrib.interpretation}", style="dim")

    return Panel(content, title=t("panel_contributors"), border_style=color)


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
    content.append(
        f"{t('tui_state', state=t(f'state_{maint.state.value}'))}\n",
        style=state_color,
    )

    if maint.last_commit_days_ago is not None:
        if maint.last_commit_days_ago == 0:
            content.append(f"{t('int_last_commit_today')}\n")
        elif maint.last_commit_days_ago == 1:
            content.append(f"{t('int_last_commit_yesterday')}\n")
        else:
            content.append(
                f"{t('tui_last_commit', days=maint.last_commit_days_ago)}\n"
            )

    if maint.commits_per_month is not None:
        content.append(f"{t('tui_frequency', rate=maint.commits_per_month)}\n")

    if maint.issue_velocity_days is not None:
        content.append(f"{t('tui_issues_closed', days=maint.issue_velocity_days)}\n")

    if maint.stale_issue_ratio is not None:
        content.append(f"{t('tui_stale_issues', ratio=maint.stale_issue_ratio)}\n")

    if maint.interpretation:
        content.append(f"\n{maint.interpretation}", style="dim")

    return Panel(content, title=t("panel_maintenance"), border_style=color)


def _render_languages(result: AnalysisResult) -> Panel:
    """Render languages panel."""
    lang = result.languages

    content = Text()

    if lang.primary:
        content.append(f"{t('tui_primary', language=lang.primary)}\n", style="bold")

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
        content.append(f"\n{t('tui_ecosystem', ecosystem=lang.ecosystem)}\n", style="dim")

    if lang.interpretation:
        content.append(f"\n{lang.interpretation}", style="dim")

    return Panel(content, title=t("panel_languages"), border_style="blue")


def _render_sustainability(result: AnalysisResult) -> Panel:
    """Render sustainability panel."""
    sust = result.sustainability
    glyph, color = _status_glyph(sust.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if sust.foundation:
        content.append(f"{t('tui_foundation', name=sust.foundation)}\n", style="green")

    if sust.funding_platforms:
        content.append(
            f"{t('tui_funding', platforms=', '.join(sust.funding_platforms))}\n"
        )

    if sust.corporate_backing:
        backing = sust.corporate_backing
        if len(backing) > 25:
            backing = backing[:22] + "..."
        content.append(f"{t('tui_corporate', company=backing)}\n")

    if sust.governance_model:
        content.append(f"{t('tui_governance', model=sust.governance_model)}\n")

    if not any([sust.foundation, sust.funding_platforms, sust.corporate_backing]):
        content.append(f"{t('tui_no_backing')}\n", style="yellow")

    if sust.interpretation:
        content.append(f"\n{sust.interpretation}", style="dim")

    return Panel(content, title=t("panel_sustainability"), border_style=color)


def _render_website(result: AnalysisResult) -> Panel:
    """Render the website availability panel."""
    site = result.website
    glyph, color = _status_glyph(site.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    if site.url:
        content.append(site.url, style="bold")
        if site.final_url and site.final_url != site.url:
            content.append(f"\n→ {site.final_url}")
        content.append("\n")

    if site.interpretation:
        content.append(f"\n{site.interpretation}", style="dim")

    return Panel(content, title=t("panel_website"), border_style=color)


def _render_security(result: AnalysisResult) -> Panel:
    """Render the pending security updates panel."""
    security = result.security
    glyph, color = _status_glyph(security.status)

    content = Text()
    content.append(f"{glyph} ", style=color)

    for update in security.updates:
        content.append(f"#{update.number} {update.title}\n")

    if security.interpretation:
        content.append(f"\n{security.interpretation}", style="dim")

    return Panel(content, title=t("panel_security"), border_style=color)


def _render_qualitative(result: AnalysisResult) -> Panel | None:
    """Render the optional LLM qualitative signals panel."""
    q = result.qualitative
    if not q.available:
        return None

    glyph, color = _status_glyph(q.status)
    content = Text()
    content.append(f"{glyph} ", style=color)

    if q.roadmap:
        content.append(f"{t('tui_roadmap', text=q.roadmap[:80])}\n")
    if q.commercial_support:
        content.append(f"{t('tui_commercial', text=q.commercial_support[:80])}\n")
    if q.security_policy:
        content.append(f"{t('tui_security', text=q.security_policy[:80])}\n")
    if q.text_maintenance_state:
        content.append(
            f"{t('tui_text_state', state=t(f'state_{q.text_maintenance_state}'))}\n"
        )

    if q.interpretation:
        content.append(f"\n{q.interpretation}", style="dim")

    return Panel(content, title=t("panel_qualitative"), border_style=color)


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
                content.append(f"  {t('tui_downloads', count=reg.downloads)}\n", style="dim")
            if reg.recent_downloads is not None:
                content.append(f"  {t('tui_recent', count=reg.recent_downloads)}\n", style="dim")

            # License info
            if reg.registry_license:
                content.append(
                    f"  {t('tui_registry_license', license=reg.registry_license)}\n",
                    style="dim",
                )
                if reg.license_matches_github is not None:
                    key = "tui_gh_license_match" if reg.license_matches_github else "tui_gh_license_diff"
                    content.append(f"  {t(key)}\n", style="dim")

            # Deprecated flag
            if reg.deprecated:
                content.append(f"  {t('tui_deprecated')}\n", style="yellow")

            # Heuristic warning
            if reg.is_heuristic:
                content.append(f"  {t('tui_heuristic')}\n", style="dim")
        else:
            content.append(f"{reg.ecosystem}: ", style="bold")
            content.append(
                f"✗ {reg.package_name} {t('tui_not_found')}\n", style="dim"
            )

    return Panel(content, title=t("panel_registries"), border_style="blue")


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
    console.print(f"[bold]{t('tui_header')}[/bold] - {result.url}", style="blue")
    console.print(f"[dim]{result.meta.description or t('tui_no_description')}[/dim]")
    console.print()

    # Summary table
    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_row(t("tui_stars", count=result.meta.stars))
    summary.add_row(t("tui_forks", count=result.meta.forks))
    if result.meta.owner_type:
        summary.add_row(
            t("tui_owner", type=t(f"owner_type_{result.meta.owner_type}"))
        )
    created = result.meta.created_at.strftime("%Y-%m-%d") if result.meta.created_at else "N/A"
    summary.add_row(t("tui_created", date=created))
    console.print(summary)
    console.print()

    # Warnings (missing token, LLM issues) — prominent, before the verdict
    warnings_panel = _render_warnings(result)
    if warnings_panel:
        console.print(warnings_panel)
        console.print()

    # Traffic-light recommendation, front and center
    console.print(_render_recommendation(result))
    console.print()

    # Optional LLM refined recommendation, right below the deterministic one
    llm_panel = _render_llm_recommendation(result)
    if llm_panel:
        console.print(llm_panel)
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

    # Website availability (always shown, even when no homepage is declared)
    console.print(_render_website(result))

    # Pending security updates
    console.print(_render_security(result))

    # Optional LLM qualitative signals panel
    qualitative_panel = _render_qualitative(result)
    if qualitative_panel:
        console.print(qualitative_panel)

    # Registries if any
    registries_panel = _render_registries(result)
    if registries_panel:
        console.print(registries_panel)

    console.print()
