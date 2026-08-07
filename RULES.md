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

2. **Pending security updates** (open Dependabot security PRs; detected by
   the dependabot-core body marker "This update includes a security fix")
   - A security update pending for more than 3 days → 🔴 "Known security
     vulnerabilities unpatched"
   - Fresh security updates (≤ 3 days, normal Dependabot flow)
     → 🟠 "Recent security updates pending"
   - No open security PR → falls through to the next branch

3. **Mirror-only repository** (no development happens here; detected by
   the GitHub `mirror_url` field or a text heuristic on description/README)
   → 🟠 "Repository is a mirror of an upstream project", with the upstream
   URL when known. The maintenance/contributor signals reflect the
   upstream, so the verdict points the user there instead.

4. **Homepage down** (only when the repository declares a homepage; repos
   without one skip both website steps)
   - DNS resolution failure, HTTP error (4xx/5xx), or redirect loop
     → 🔴 "Project homepage is down"
   - Timeout, or page behind a bot-protection check ("I'm not a robot")
     → 🟠 "Project homepage unreachable or bot-protected"

5. **Ephemeral project** (created < 6 months, ≤ 200 stars, ≤ 3 authors)
   → 🟠 "Ephemeral project accompanying an article"
   - **Exception:** organization-owned repositories are never judged
     ephemeral (`owner_type == "organization"`) — an organization does
     not create a repo merely to accompany an article, so the "weekend
     demo" heuristic does not apply.

6. **Abandoned** (no commit for 6+ months, i.e. `MaintenanceState.ABANDONED`)
   - Widely used (≥ 5k stars, ≥ 1k forks, or ≥ 1M registry downloads)
     → 🟠 "Large project, but now abandoned"
   - Otherwise → 🔴 "Abandoned project — no commit for N months"

7. **Active development** (`MaintenanceState.ACTIVE`)
   - ≥ 80% of commits from bots → 🟠 "Project maintained only by dependency-update bots"
   - No stable release (none, pre-release, or 0.x) → 🟠 "Active development but not yet stabilized"
   - Declining activity (3-month commits < 25% of 12-month commits) → 🟠 "Well-maintained project but in decline"
   - Large community (≥ 100 human authors or ≥ 10k stars), **or LLM-enabled with roadmap AND commercial support** → 🟢 "Active project with a large community"
   - Otherwise → 🟢 "Active project"

8. **Maintenance mode** (infrequent commits, issues still closed)
   - Last release more than 6 months ago → 🟠 "Well-maintained but no new features for N months"
   - Otherwise → 🟠 "Project in maintenance mode"

9. **LLM-reported discontinuation** (only when the LLM is enabled AND the
   maintenance state is unknown — commit data wins over prose)
   - Widely used → 🟠 "Large project, but now abandoned"
   - Otherwise → 🔴 "Project texts announce its discontinuation"

10. **Unknown maintenance state**
   - Widely used → 🟠 "Widely used project despite low maintenance"
   - LLM enabled, text declares active development AND (roadmap or
     commercial support) → 🟢 "Active project"
   - Otherwise → 🟠 "Insufficient data for a reliable recommendation"

**Modifier — exotic main language (downgrade only):** when the verdict
would be green, an uncommon main language (outside the PYPL top-20 and
the GitHub Innovation Graph top-20, see the language datasets below)
downgrades it to 🟠 "Main language is uncommon". The rule never upgrades
a verdict and never fires on red/orange outcomes: red flags, a dead
homepage, abandonment, bot domination, etc. all keep their verdict.

**Informational signal — README language:** whether the README is written
in English is displayed in the report (dependency-free heuristic on the
dominant script and English stopword density) but **never affects the
verdict**.

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

### Website probe (`fetchers/website.py`)

The homepage probe constants live in `fetchers/website.py` (not
`recommendation.py`):

| Constant | Value | Used for |
|----------|-------|----------|
| `_TIMEOUT` | connect 10 s / read 15 s | homepage probe timeouts |
| `_MAX_REDIRECTS` | 10 | redirects followed before failing |
| `_CAPTCHA_SAMPLE_BYTES` | 64 KiB | body sample for the bot-protection heuristic |

### Security updates (`analyzers/security.py`)

| Constant | Value | Used for |
|----------|-------|----------|
| `_PENDING_LIMIT_DAYS` | 3 | a security update pending longer than this is critical |

Pending updates are open Dependabot PRs detected by the dependabot-core
body marker "This update includes a security fix." (plain version bumps
mention "security" only inside the dependency changelog and are ignored).

The bot-protection heuristic flags a page as captcha-protected when the
response headers, title or first 64 KiB of HTML contain markers such as
`g-recaptcha`, `hcaptcha`, Cloudflare `turnstile`/`cf-mitigated`, or the
phrases "not a robot" / "verify you are human".

### Main-language popularity (`data/` + `scripts/refresh_language_datasets.py`)

A main language is **popular** when it appears in the top-20 of the PYPL
index or the top-20 by pushers of the GitHub Innovation Graph. Both
datasets are committed under `src/gh_score/data/` and refreshed with
`python scripts/refresh_language_datasets.py --top 20`:

| File | Source | Columns |
|------|--------|---------|
| `pypl_languages.csv` | https://pypl.github.io/PYPL.html | rank, language, share |
| `github_languages.csv` | github/innovationgraph `data/languages.csv` (most recent quarter) | rank, language, num_pushers |

The top-N threshold is a parameter of the refresh script (default 20).

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
| `rec_site_down` | La page d'accueil du projet est hors ligne | Project homepage is down |
| `rec_site_degraded` | Page d'accueil injoignable ou protégée par un anti-robot | Project homepage unreachable or bot-protected |
| `reason_site_down` | la page d'accueil est inaccessible | the project homepage is unreachable |
| `reason_site_dns` | le nom de domaine de la page d'accueil ne résout pas | the homepage domain name does not resolve |
| `reason_site_http` | la page d'accueil répond HTTP {code} | the homepage answers HTTP {code} |
| `reason_site_redirect` | boucle de redirection sur la page d'accueil | redirect loop on the homepage |
| `reason_site_timeout` | la page d'accueil a expiré (timeout) | the homepage timed out |
| `reason_site_captcha` | la page d'accueil est protégée par un contrôle anti-robot | the homepage is behind a bot-protection check |
| `rec_language_exotic` | Langage principal peu répandu | Main language is uncommon |
| `reason_language_exotic` | le langage principal ({language}) est peu répandu (hors top 20 PYPL et GitHub Innovation Graph) | the main language ({language}) is uncommon (outside the PYPL and GitHub Innovation Graph top 20) |
| `rec_security_pending` | Mises à jour de sécurité récentes en attente | Recent security updates pending |
| `rec_security_overdue` | Vulnérabilités de sécurité connues non corrigées | Known security vulnerabilities unpatched |
| `reason_security_pending` | {count} mise(s) de sécurité en attente | {count} security update(s) pending |
| `reason_security_overdue` | une mise à jour de sécurité est en attente depuis {days} jours | a security update has been pending for {days} days |
| `rec_mirror` | Dépôt miroir d'un projet amont | Repository is a mirror of an upstream project |
| `reason_mirror` | ce dépôt est un miroir — le développement a lieu ailleurs | this repository is a mirror — development happens elsewhere |
| `reason_mirror_upstream` | ce dépôt est un miroir de {upstream} | this repository is a mirror of {upstream} |
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
