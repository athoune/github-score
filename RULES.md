# Scoring Rules

How `gh-score` turns raw indicators into a single traffic-light verdict
(recommendation). The verdict is computed by
`gh_score.core.analyzers.recommendation.analyze_recommendation` from the
other indicator families (maintenance, contributors, release health,
sustainability, registries, metadata).

Messages are localized via `gh_score.i18n` (French and English, selected
from `$LANG` / `LC_ALL` / `LC_MESSAGES`; fallback: English).

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

3. **Abandoned** (no commit for 6+ months, i.e. `MaintenanceState.ABANDONED`)
   - Widely used (≥ 5k stars, ≥ 1k forks, or ≥ 1M registry downloads)
     → 🟠 "Large project, but now abandoned"
   - Otherwise → 🔴 "Abandoned project — no commit for N months"

4. **Active development** (`MaintenanceState.ACTIVE`)
   - ≥ 80% of commits from bots → 🟠 "Project maintained only by dependency-update bots"
   - No stable release (none, pre-release, or 0.x) → 🟠 "Active development but not yet stabilized"
   - Declining activity (3-month commits < 25% of 12-month commits) → 🟠 "Well-maintained project but in decline"
   - Large community (≥ 100 human authors or ≥ 10k stars) → 🟢 "Active project with a large community"
   - Otherwise → 🟢 "Active project"

5. **Maintenance mode** (infrequent commits, issues still closed)
   - Last release more than 6 months ago → 🟠 "Well-maintained but no new features for N months"
   - Otherwise → 🟠 "Project in maintenance mode"

6. **Unknown maintenance state**
   - Widely used → 🟠 "Widely used project despite low maintenance"
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
sustainability) whose status is known (not `UNKNOWN`). It is displayed as
a percentage in the UI. It measures how much data the verdict is based
on, not how "good" the project is.

## Reasoning

Each verdict carries a `reasoning` list: the triggering signal plus
objective facts (stars, author count). Example:

```
🟢 Active project with a large community
  • active state, regular development
  • 15,000 stars
  • 150 authors
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

The full catalog (including reasoning lines, facts and UI labels) lives
in `src/gh_score/i18n.py`. To add a language, add a catalog dict there
and a test in `tests/test_i18n.py`.
