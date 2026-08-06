# Scoring Rules

How `gh-score` turns raw indicators into a single traffic-light verdict
(recommendation). The verdict is computed by
`gh_score.core.analyzers.recommendation.analyze_recommendation` from the
other indicator families (maintenance, contributors, release health,
sustainability, registries, metadata).

All user-facing strings are localized through `gh_score.i18n` (French
and English, selected from `$LANG` / `LC_ALL` / `LC_MESSAGES`; fallback:
English). This includes the recommendation messages below as well as
every indicator interpretation, TUI label, Markdown heading and CLI
message. Key groups: `rec_*`, `int_*`, `state_*`/`status_*`, `tui_*`,
`md_*`, `cli_*`. Technical strings (exceptions, click help, JSON enum
values) stay in English.

## Traffic-light colors

| Color | Meaning | Verdict |
|-------|---------|---------|
| 🟢 green | **Active project** — safe to bet on | reliable |
| 🟠 orange | **Potential but not stable**, or widely used despite low maintenance | proceed with caution |
| 🔴 red | **Risky** — lack of maintenance | avoid for new dependencies |

## Decision tree

The rules are evaluated **in order**; the first matching branch wins.

1. **Hard red flags**
   - Repository archived → 🔴 "Archived project — no further development"
   - Repository disabled → 🔴 "Disabled project"
   - Package marked deprecated on a registry → 🔴 "Project deprecated on the registry"

2. **Ephemeral project** (created < 6 months, ≤ 200 stars, ≤ 3 authors)
   → 🟠 "Ephemeral project accompanying an article"
   - **Exception:** organization-owned repositories are never judged
     ephemeral (`owner_type == "organization"`) — an organization does
     not create a repo merely to accompany an article, so the "weekend
     demo" heuristic does not apply.

3. **Abandoned** (no commit for 6+ months, i.e. `MaintenanceState.ABANDONED`)
   - Widely used (≥ 5k stars, ≥ 1k forks, or ≥ 1M registry downloads)
     → 🟠 "Large project, but now abandoned"
   - Otherwise → 🔴 "Abandoned project — no commit for N months"

4. **Active development** (`MaintenanceState.ACTIVE`)
   - ≥ 80% of commits from bots → 🟠 "Project maintained only by dependency-update bots"
   - No stable release (none, pre-release, or 0.x) → 🟠 "Active development but not yet stabilized"
   - Declining activity (3-month commits < 25% of 12-month commits) → 🟠 "Well-maintained project but in decline"
   - Large community (≥ 100 human authors or ≥ 10k stars), **or LLM-enabled with roadmap AND commercial support** → 🟢 "Active project with a large community"
   - Otherwise → 🟢 "Active project"

5. **Maintenance mode** (infrequent commits, issues still closed)
   - Last release more than 6 months ago → 🟠 "Well-maintained but no new features for N months"
   - Otherwise → 🟠 "Project in maintenance mode"

6. **LLM-reported discontinuation** (only when the LLM is enabled AND the
   maintenance state is unknown — commit data wins over prose)
   - Widely used → 🟠 "Large project, but now abandoned"
   - Otherwise → 🔴 "Project texts announce its discontinuation"

7. **Unknown maintenance state**
   - Widely used → 🟠 "Widely used project despite low maintenance"
   - LLM enabled, text declares active development AND (roadmap or
     commercial support) → 🟢 "Active project"
   - Otherwise → 🟠 "Insufficient data for a reliable recommendation"

## Thresholds

All thresholds are module-level constants in `recommendation.py` and are
heuristics to be tuned with real-world examples.

| Constant | Value | Used for |
|----------|-------|----------|
| `_WIDELY_USED_STARS` | 5 000 | "widely used" mitigation |
| `_WIDELY_USED_FORKS` | 1 000 | "widely used" mitigation |
| `_WIDELY_USED_DOWNLOADS` | 1 000 000 | registry downloads mitigation |
| `_LARGE_COMMUNITY_AUTHORS` | 100 | green "large community" |
| `_LARGE_COMMUNITY_STARS` | 10 000 | green "large community" |
| `_BOT_DOMINATED_RATIO` | 0.8 | bot-dominated warning |
| `_DECLINING_FACTOR` | 0.25 | decline detection |
| `_NO_RELEASE_MONTHS` | 6 | "no new features" warning |
| `_EPHEMERAL_AGE_DAYS` | 180 | ephemeral project detection |
| `_EPHEMERAL_MAX_AUTHORS` | 3 | ephemeral project detection |
| `_EPHEMERAL_MAX_STARS` | 200 | ephemeral project detection |

## Confidence

`confidence` (0.0 → 1.0) reflects **data completeness**: the fraction of
the four core indicators (maintenance, contributors, release health,
sustainability) whose status is known (not `UNKNOWN`), plus the qualitative
indicator when the LLM ran. It is displayed as a percentage in the UI. It
measures how much data the verdict is based on, not how "good" the project
is.

## Refined recommendation (LLM, optional)

When the LLM is enabled, a second, complementary verdict is produced:
the LLM receives a compact digest of **every indicator family** (metadata,
maintenance, contributors, releases, license, sustainability, qualitative
signals, registries) plus the deterministic traffic-light verdict, and
returns a nuanced recommendation: a level (`green` / `orange` / `red`), a
short message, a 2-4 sentence explanation weighing the signals and
trade-offs, and a self-assessed confidence.

Rules:

- The refined recommendation is **informational and complementary**. It
  never replaces the deterministic verdict: both are displayed, and the
  traffic light of the report stays the deterministic one.
- The LLM may agree with, or refine/disagree with, the deterministic
  verdict — the two are shown side by side so the user sees the nuance.
- Invalid LLM output (unknown level, unparseable JSON, provider failure)
  simply hides the refined panel; the pipeline never breaks.

## Reasoning

Each verdict carries a `reasoning` list: the triggering signal plus
objective facts (stars, author count, owner type) and, when the LLM is
enabled, qualitative facts (roadmap, commercial support, security policy,
declared maintenance state). Example:

```
🟢 Active project with a large community
  • active state, regular development
  • 15,000 stars
  • 150 authors
  • owner: organization
  • roadmap announced
  • commercial support available
```

## Message catalog

| Key | French | English |
|-----|--------|---------|
| `rec_archived` | Projet archivé — plus aucun développement | Archived project — no further development |
| `rec_disabled` | Projet désactivé | Disabled project |
| `rec_deprecated` | Projet déprécié sur le registre | Project deprecated on the registry |
| `rec_ephemeral` | Projet éphémère accompagnant un article | Ephemeral project accompanying an article |
| `rec_abandoned_popular` | Grand projet, mais maintenant abandonné | Large project, but now abandoned |
| `rec_abandoned_months` | Projet abandonné — pas de commit depuis {months} mois | Abandoned project — no commit for {months} months |
| `rec_bots` | Projet uniquement maintenu par des bots qui mettent à jour les dépendances | Project maintained only by dependency-update bots |
| `rec_not_stable` | Projet en développement actif mais pas encore stabilisé | Active development but not yet stabilized |
| `rec_declining` | Projet bien maintenu mais en déclin | Well-maintained project but in decline |
| `rec_active_community` | Projet actif avec une grande communauté | Active project with a large community |
| `rec_active` | Projet actif | Active project |
| `rec_maintenance_no_release` | Projet bien maintenu mais sans nouveautés depuis {months} mois | Well-maintained but no new features for {months} months |
| `rec_maintenance` | Projet en mode maintenance | Project in maintenance mode |
| `rec_widely_used_unmaintained` | Projet largement utilisé même si peu maintenu | Widely used project despite low maintenance |
| `rec_insufficient_data` | Données insuffisantes pour une recommandation fiable | Insufficient data for a reliable recommendation |
| `rec_text_discontinued` | Les textes du projet annoncent son abandon | Project texts announce its discontinuation |
| `fact_owner` | propriétaire : {type} | owner: {type} |
| `owner_type_user` | utilisateur | user |
| `owner_type_organization` | organisation | organization |
| `fact_roadmap` | feuille de route annoncée | roadmap announced |
| `fact_commercial` | support commercial disponible | commercial support available |
| `fact_security` | politique de sécurité documentée | security policy documented |
| `panel_llm_recommendation` | Recommandation affinée (LLM) | Refined recommendation (LLM) |
| `md_section_llm_recommendation` | ## Recommandation affinée (LLM) | ## Refined recommendation (LLM) |

The full catalog (including reasoning lines, facts and UI labels) lives
in `src/gh_score/i18n.py`. To add a language, add a catalog dict there
and a test in `tests/test_i18n.py`.
