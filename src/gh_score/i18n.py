"""Lightweight internationalization for user-facing strings.

The active language is derived from the environment using the standard
locale precedence: ``LC_ALL`` > ``LC_MESSAGES`` > ``LANG``.  Locale
strings like ``fr_FR.UTF-8`` or ``fr-FR`` are reduced to their language
code (``fr``).  Unset or unknown locales fall back to English.

This is intentionally dependency-free: no gettext catalogs, no build
step.  Messages live in a plain dict keyed by stable identifiers, with
one catalog per supported language.

Conventions:
- ``rec_*``   — recommendation verdicts and reasoning
- ``int_*``   — analyzer interpretations (stored on indicators)
- ``state_*`` / ``status_*`` — enum value display labels
- ``tui_*``   — TUI dashboard labels
- ``md_*``    — Markdown report labels
- ``cli_*``   — CLI console messages

Technical/exception strings (error messages, click help, enum values in
JSON) intentionally stay in English.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Message catalog
# ---------------------------------------------------------------------------
# Keys are stable identifiers used by the code.  Values may contain
# str.format placeholders, e.g. "{months}" or "{ratio:.0%}".

MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        # ------------------------------------------------------------------
        # Recommendation verdicts
        # ------------------------------------------------------------------
        "rec_archived": "Projet archivé — plus aucun développement",
        "rec_disabled": "Projet désactivé",
        "rec_deprecated": "Projet déprécié dans son index de paquets",
        "rec_ephemeral": "Projet éphémère accompagnant un article",
        "rec_abandoned_popular": "Grand projet, mais maintenant abandonné",
        "rec_abandoned_months": "Projet abandonné — pas de commit depuis {months} mois",
        "rec_bots": (
            "Projet uniquement maintenu par des bots qui mettent à jour "
            "les dépendances"
        ),
        "rec_not_stable": "Projet en développement actif mais pas encore stabilisé",
        "rec_declining": "Projet bien maintenu mais en déclin",
        "rec_active_community": "Projet actif avec une grande communauté",
        "rec_active": "Projet actif",
        "rec_maintenance_no_release": (
            "Projet bien maintenu mais sans nouveautés depuis {months} mois"
        ),
        "rec_maintenance": "Projet en mode maintenance",
        "rec_widely_used_unmaintained": "Projet largement utilisé même si peu maintenu",
        "rec_insufficient_data": "Données insuffisantes pour une recommandation fiable",
        "rec_site_down": "La page d'accueil du projet est hors ligne",
        "rec_site_degraded": "Page d'accueil injoignable ou protégée par un anti-robot",
        "rec_language_exotic": "Langage principal peu répandu",
        "rec_security_pending": "Mises à jour de sécurité récentes en attente",
        "rec_mirror": "Dépôt miroir d'un projet amont",
        "rec_security_overdue": "Vulnérabilités de sécurité connues non corrigées",
        "rec_text_discontinued": "Les textes du projet annoncent son abandon",

        # Recommendation reasoning lines
        "reason_archived": "le dépôt est marqué comme archivé sur GitHub",
        "reason_disabled": "le dépôt est désactivé sur GitHub",
        "reason_deprecated": "le paquet est marqué comme déprécié",
        "reason_ephemeral": "créé récemment, faible audience, très peu d'auteurs",
        "reason_abandoned_popular": (
            "pas de commit depuis longtemps malgré une large adoption"
        ),
        "reason_last_commit_days": "dernier commit il y a {days} jours",
        "reason_bots": "{ratio:.0%} des commits proviennent de bots",
        "reason_no_stable_release": "pas de release stable (1.0+ ou non-pré-release)",
        "reason_declining": "l'activité des 3 derniers mois est en nette baisse",
        "reason_active": "état actif, développement régulier",
        "reason_release_age_days": "dernière release il y a {days} jours",
        "reason_maintenance": "correctifs apportés mais pas de développement actif",
        "reason_unknown_widely_used": (
            "état de maintenance incertain mais large adoption"
        ),
        "reason_insufficient": "trop peu de données exploitables sur la maintenance",
        "reason_text_discontinued": (
            "le README/GOVERNANCE déclare explicitement que le projet n'est "
            "plus maintenu"
        ),
        "reason_site_down": "la page d'accueil est inaccessible",
        "reason_site_dns": "le nom de domaine de la page d'accueil ne résout pas",
        "reason_site_http": "la page d'accueil répond HTTP {code}",
        "reason_site_redirect": "boucle de redirection sur la page d'accueil",
        "reason_site_timeout": "la page d'accueil a expiré (timeout)",
        "reason_site_captcha": "la page d'accueil est protégée par un contrôle anti-robot",
        "reason_language_exotic": "le langage principal ({language}) est peu répandu (hors top 20 PYPL et GitHub Innovation Graph)",
        "reason_security_pending": "{count} mise(s) de sécurité en attente",
        "reason_security_overdue": "une mise à jour de sécurité est en attente depuis {days} jours",
        "reason_mirror": "ce dépôt est un miroir — le développement a lieu ailleurs",
        "reason_mirror_upstream": "ce dépôt est un miroir de {upstream}",
        "reason_text_active": "le texte du projet déclare un développement actif",

        # Objective facts appended to the reasoning
        "fact_stars": "{stars:,} étoiles",
        "fact_authors": "{authors} auteurs",
        "fact_owner": "propriétaire : {type}",
        "fact_roadmap": "feuille de route annoncée",
        "fact_commercial": "support commercial disponible",
        "fact_security": "politique de sécurité documentée",
        "fact_funding": "financement disponible",
        "fact_corporate": "soutien d'une entreprise",
        "fact_foundation": "adossé à une fondation",

        # ------------------------------------------------------------------
        # Release health interpretation
        # ------------------------------------------------------------------
        "int_release_latest": "Dernière : {version}",
        "int_released_today": "publiée aujourd'hui",
        "int_released_yesterday": "publiée hier",
        "int_released_days_ago": "publiée il y a {days} jours",
        "int_cadence_very_active": "cadence : ~{days:.0f}d/release (très actif)",
        "int_cadence_active": "cadence : ~{days:.0f}d/release (actif)",
        "int_cadence_moderate": "cadence : ~{days:.0f}d/release (modéré)",
        "int_cadence_slow": "cadence : ~{days:.0f}d/release (lent)",
        "int_semver_yes": "semver : oui",
        "int_semver_no": "semver : non",
        "int_prerelease": "pré-release",
        "int_no_release_data": "Aucune donnée de release",

        # ------------------------------------------------------------------
        # License interpretation
        # ------------------------------------------------------------------
        "int_lic_family_permissive": "permissive",
        "int_lic_family_copyleft": "copyleft",
        "int_lic_family_public_domain": "domaine public",
        "int_lic_family_proprietary": "propriétaire",
        "int_lic_family_other": "autre",
        "int_lic_family_unknown": "inconnue",
        "int_lic_osi_approved": "approuvée OSI",
        "int_lic_none": "Aucune licence détectée",

        # ------------------------------------------------------------------
        # Contributors interpretation
        # ------------------------------------------------------------------
        "int_authors": "{count} auteurs",
        "int_bus_factor": "facteur du bus : {count}",
        "int_bots": "bots : {ratio:.0%}",
        "int_lead": "développeur principal : {login}",
        "int_historical_lead": "historique : {login}",
        "int_minor": "{count} mineurs",
        "int_commits_3m": "{count} commits (3m)",
        "int_commits_12m": "{count} commits (12m)",
        "int_no_activity": "aucune activité récente",

        # ------------------------------------------------------------------
        # Maintenance interpretation
        # ------------------------------------------------------------------
        "int_state": "état : {state}",
        "int_last_commit_today": "dernier commit : aujourd'hui",
        "int_last_commit_yesterday": "dernier commit : hier",
        "int_last_commit_days": "dernier commit : il y a {days} j",
        "int_cpm_very_active": "{rate:.1f} commits/mois (très actif)",
        "int_cpm_active": "{rate:.1f} commits/mois (actif)",
        "int_cpm_low": "{rate:.1f} commits/mois (faible)",
        "int_cpm_none": "aucun commit récent",
        "int_issues_lt1d": "tickets fermés : <1 j",
        "int_issues_days": "tickets fermés : {days:.0f} j",
        "int_issues_moderate": "tickets fermés : {days:.0f} j (modéré)",
        "int_issues_slow": "tickets fermés : {days:.0f} j (lent)",
        "int_stale_issues": "{ratio:.0%} de tickets en souffrance",

        # ------------------------------------------------------------------
        # Website interpretation
        # ------------------------------------------------------------------
        "int_site_no_homepage": "Pas de page d'accueil déclarée",
        "int_site_ok": "Site accessible (HTTP {code})",
        "int_site_dns": "Le nom de domaine ne résout pas : {site}",
        "int_site_timeout": "Le site a expiré (timeout) : {site}",
        "int_site_http": "Le site répond HTTP {code} : {site}",
        "int_site_redirect": "Boucle de redirection sur le site : {site}",
        "int_site_captcha": "Site protégé par un contrôle anti-robot (« I'm not a robot ») : {site}",
        "int_site_unreachable": "Site injoignable : {site}",

        # ------------------------------------------------------------------
        # Security updates interpretation
        # ------------------------------------------------------------------
        "int_security_none": "Aucune mise à jour de sécurité en attente",
        "int_security_pending": "{count} mise(s) à jour de sécurité en attente",
        "int_security_overdue": "{count} mise(s) à jour de sécurité en attente depuis {days} jours",

        # ------------------------------------------------------------------
        # Languages interpretation
        # ------------------------------------------------------------------
        "int_primary": "principal : {language}",
        "int_language_popular": "langage répandu (top {rank})",
        "int_language_exotic": "langage principal peu répandu ({language})",
        "int_readme_english": "README en anglais",
        "int_readme_not_english": "README dans une autre langue que l'anglais",
        "int_breakdown": "répartition : {langs}",
        "int_ecosystem": "écosystème : {ecosystem}",
        "int_no_language": "Aucune donnée de langage",

        # ------------------------------------------------------------------
        # Sustainability interpretation
        # ------------------------------------------------------------------
        "int_foundation": "fondation : {name}",
        "int_funding": "financement : {platforms}",
        "int_corporate": "entreprise : {company}",
        "int_governance": "gouvernance : {model}",
        "int_no_backing": "aucun soutien détecté",
        "int_roadmap": "roadmap : {text}",
        "int_commercial": "support commercial : {text}",
        "int_security": "sécurité : {text}",
        "int_text_state": "état déclaré : {state}",

        # ------------------------------------------------------------------
        # Status / state display labels
        # ------------------------------------------------------------------
        "status_healthy": "sain",
        "status_warning": "attention",
        "status_critical": "critique",
        "status_unknown": "inconnu",
        "state_active": "actif",
        "state_maintenance": "maintenance",
        "state_abandoned": "abandonné",
        "state_unknown": "inconnu",
        "owner_type_user": "utilisateur",
        "owner_type_organization": "organisation",

        # ------------------------------------------------------------------
        # TUI dashboard
        # ------------------------------------------------------------------
        "tui_header": "Tableau de bord de la santé d'un projet GitHub",
        "tui_no_description": "Aucune description",
        "tui_stars": "Étoiles : {count:,}",
        "tui_forks": "Forks : {count:,}",
        "tui_created": "Créé : {date}",
        "tui_owner": "Propriétaire : {type}",
        "panel_release_health": "Santé des releases",
        "panel_license": "Licence",
        "panel_contributors": "Contributeurs",
        "panel_maintenance": "Maintenance",
        "panel_languages": "Langages",
        "panel_sustainability": "Durabilité",
        "panel_registries": "Registres de paquets",
        "panel_qualitative": "Signaux qualitatifs",
        "panel_website": "Site web",
        "panel_security": "Sécurité",
        "tui_mirror": "⚠ Dépôt miroir — le développement a lieu ailleurs",
        "tui_mirror_upstream": "⚠ Miroir de {upstream}",
        "panel_llm_recommendation": "Recommandation affinée (LLM)",
        "tui_latest": "dernière : {version}",
        "tui_age": "âge : {days} jours",
        "tui_cadence": "cadence : {days:.0f} jours/release",
        "tui_semver_yes": "semver : oui",
        "tui_semver_no": "semver : non",
        "tui_prerelease": "statut : pré-release",
        "tui_no_license": "Aucune licence détectée",
        "tui_total": "total : {count}",
        "tui_bus_factor": "facteur de bus : {count}",
        "tui_bots": "bots : {ratio:.0%}",
        "tui_lead": "lead : {login}",
        "tui_historical": "historique : {login}",
        "tui_minor": "mineurs : {count}",
        "tui_activity_3m": "activité : {count} commits (3m)",
        "tui_activity_12m": "activité : {count} commits (12m)",
        "tui_state": "état : {state}",
        "tui_last_commit": "dernier commit : il y a {days} j",
        "tui_frequency": "fréquence : {rate:.1f} commits/mois",
        "tui_issues_closed": "issues fermées : {days:.0f} j",
        "tui_stale_issues": "issues en souffrance : {ratio:.0%}",
        "tui_primary": "principal : {language}",
        "tui_ecosystem": "écosystème : {ecosystem}",
        "tui_foundation": "fondation : {name}",
        "tui_funding": "financement : {platforms}",
        "tui_corporate": "entreprise : {company}",
        "tui_governance": "gouvernance : {model}",
        "tui_no_backing": "aucun soutien détecté",
        "tui_downloads": "téléchargements : {count:,}",
        "tui_recent": "récents : {count:,}",
        "tui_registry_license": "licence : {license}",
        "tui_gh_license_match": "licence GitHub : identique",
        "tui_gh_license_diff": "licence GitHub : différente",
        "tui_deprecated": "⚠ déprécié",
        "tui_heuristic": "(nom déduit du dépôt)",
        "tui_not_found": "(introuvable)",
        "tui_roadmap": "roadmap : {text}",
        "tui_commercial": "support commercial : {text}",
        "tui_security": "sécurité : {text}",
        "tui_text_state": "état déclaré : {state}",
        "ui_confidence": "confiance : {conf:.0%}",

        # ------------------------------------------------------------------
        # Warnings
        # ------------------------------------------------------------------
        "warn_no_token": (
            "Token GitHub non défini — requêtes anonymes limitées (60/h)"
        ),
        "warn_llm_unavailable": (
            "LLM configuré mais injoignable ou réponse invalide — "
            "signaux qualitatifs indisponibles"
        ),
        "warn_llm_no_api_key": (
            "LLM distant configuré sans clé API (GH_SCORE_LLM_API_KEY)"
        ),
        "warn_llm_contradiction": (
            "La recommandation affinée (LLM) semble contredire les signaux "
            "extraits (nie : {facts})"
        ),
        "panel_warnings": "Avertissements",

        # ------------------------------------------------------------------
        # Markdown report
        # ------------------------------------------------------------------
        "md_section_recommendation": "## Recommendation",
        "md_section_release_health": "## Santé des releases",
        "md_section_license": "## Licence",
        "md_section_contributors": "## Contributeurs",
        "md_section_maintenance": "## Maintenance",
        "md_section_languages": "## Langages",
        "md_section_sustainability": "## Durabilité",
        "md_section_qualitative": "## Signaux qualitatifs",
        "md_section_llm_recommendation": "## Recommandation affinée (LLM)",
        "md_section_website": "## Site web",
        "md_section_security": "## Sécurité",
        "md_header_stars": "**Étoiles :** {count:,}",
        "md_header_forks": "**Forks :** {count:,}",
        "md_owner": "**Propriétaire :** {type}",
        "md_latest": "**Dernière version :** {version}",
        "md_age": "**Âge :** {days} jours",
        "md_cadence": "**Cadence :** {days:.0f} jours/release",
        "md_status": "**Statut :** {status}",
        "md_license_label": "**Licence :** {spdx} ({family})",
        "md_license_none": "**Licence :** Non détectée",
        "md_total_authors": "**Nombre d'auteurs :** {count}",
        "md_bus_factor": "**Facteur de bus :** {count}",
        "md_bot_ratio": "**Ratio de bots :** {ratio:.0%}",
        "md_lead": "**Développeur principal :** {login}",
        "md_state": "**État :** {state}",
        "md_last_commit": "**Dernier commit :** il y a {days} jours",
        "md_frequency": "**Fréquence :** {rate:.1f} commits/mois",
        "md_primary": "**Principal :** {language}",
        "md_breakdown": "**Répartition :**",
        "md_foundation": "**Fondation :** {name}",
        "md_funding": "**Financement :** {platforms}",
        "md_corporate": "**Soutien d'entreprise :** {company}",
        "md_roadmap": "**Roadmap :** {text}",
        "md_commercial": "**Support commercial :** {text}",
        "md_security": "**Sécurité :** {text}",
        "md_text_state": "**État déclaré :** {state}",
        "md_confidence": "Confiance : {conf:.0%}",

        # ------------------------------------------------------------------
        # CLI console messages
        # ------------------------------------------------------------------
        "cli_analyzing": "Analyse du dépôt...",
        "cli_cache_cleared": "Cache vidé",
        "cli_error": "Erreur :",
        "cli_no_target": (
            "Aucune URL fournie et le répertoire courant n'est pas un dépôt git."
        ),
        "cli_usage_1": "Usage : gh-score [URL] [OPTIONS]",
        "cli_usage_2": "Usage : gh-score analyze [URL] [OPTIONS]",
        "cli_config_title": "Configuration actuelle",
        "cli_config_setting": "Paramètre",
        "cli_config_value": "Valeur",
        "cli_cfg_token": "Token GitHub",
        "cli_cfg_set": "défini",
        "cli_cfg_not_set": "non défini",
        "cli_cfg_cache_dir": "Dossier de cache",
        "cli_cfg_cache_ttl": "TTL du cache",
        "cli_cfg_llm_enabled": "LLM activé",
        "cli_cfg_llm_provider": "Fournisseur LLM",
        "cli_cfg_llm_model": "Modèle LLM",
        "cli_cfg_llm_base_url": "URL de base LLM",
    },
    "en": {
        # ------------------------------------------------------------------
        # Recommendation verdicts
        # ------------------------------------------------------------------
        "rec_archived": "Archived project — no further development",
        "rec_disabled": "Disabled project",
        "rec_deprecated": "Project deprecated on the registry",
        "rec_ephemeral": "Ephemeral project accompanying an article",
        "rec_abandoned_popular": "Large project, but now abandoned",
        "rec_abandoned_months": "Abandoned project — no commit for {months} months",
        "rec_bots": "Project maintained only by dependency-update bots",
        "rec_not_stable": "Active development but not yet stabilized",
        "rec_declining": "Well-maintained project but in decline",
        "rec_active_community": "Active project with a large community",
        "rec_active": "Active project",
        "rec_maintenance_no_release": (
            "Well-maintained but no new features for {months} months"
        ),
        "rec_maintenance": "Project in maintenance mode",
        "rec_widely_used_unmaintained": "Widely used project despite low maintenance",
        "rec_insufficient_data": "Insufficient data for a reliable recommendation",
        "rec_site_down": "Project homepage is down",
        "rec_site_degraded": "Project homepage unreachable or bot-protected",
        "rec_language_exotic": "Main language is uncommon",
        "rec_security_pending": "Recent security updates pending",
        "rec_mirror": "Repository is a mirror of an upstream project",
        "rec_security_overdue": "Known security vulnerabilities unpatched",
        "rec_text_discontinued": "Project texts announce its discontinuation",

        # Recommendation reasoning lines
        "reason_archived": "repository marked as archived on GitHub",
        "reason_disabled": "repository disabled on GitHub",
        "reason_deprecated": "package marked as deprecated",
        "reason_ephemeral": "recently created, small audience, very few authors",
        "reason_abandoned_popular": "no commit for a long time despite wide adoption",
        "reason_last_commit_days": "last commit {days} days ago",
        "reason_bots": "{ratio:.0%} of commits come from bots",
        "reason_no_stable_release": "no stable release (1.0+ or non-pre-release)",
        "reason_declining": "activity over the last 3 months is sharply down",
        "reason_active": "active state, regular development",
        "reason_release_age_days": "last release {days} days ago",
        "reason_maintenance": "bug fixes but no active development",
        "reason_unknown_widely_used": "uncertain maintenance state but wide adoption",
        "reason_insufficient": "too little usable maintenance data",
        "reason_text_discontinued": (
            "README/GOVERNANCE explicitly states the project is no longer "
            "maintained"
        ),
        "reason_site_down": "the project homepage is unreachable",
        "reason_site_dns": "the homepage domain name does not resolve",
        "reason_site_http": "the homepage answers HTTP {code}",
        "reason_site_redirect": "redirect loop on the homepage",
        "reason_site_timeout": "the homepage timed out",
        "reason_site_captcha": "the homepage is behind a bot-protection check",
        "reason_language_exotic": "the main language ({language}) is uncommon (outside the PYPL and GitHub Innovation Graph top 20)",
        "reason_security_pending": "{count} security update(s) pending",
        "reason_security_overdue": "a security update has been pending for {days} days",
        "reason_mirror": "this repository is a mirror — development happens elsewhere",
        "reason_mirror_upstream": "this repository is a mirror of {upstream}",
        "reason_text_active": "the project text declares active development",

        # Objective facts appended to the reasoning
        "fact_stars": "{stars:,} stars",
        "fact_authors": "{authors} authors",
        "fact_owner": "owner: {type}",
        "fact_roadmap": "roadmap announced",
        "fact_commercial": "commercial support available",
        "fact_security": "security policy documented",
        "fact_funding": "funding available",
        "fact_corporate": "corporate backing",
        "fact_foundation": "foundation-backed",

        # ------------------------------------------------------------------
        # Release health interpretation
        # ------------------------------------------------------------------
        "int_release_latest": "Latest: {version}",
        "int_released_today": "released today",
        "int_released_yesterday": "released yesterday",
        "int_released_days_ago": "released {days} days ago",
        "int_cadence_very_active": "cadence: ~{days:.0f}d/release (very active)",
        "int_cadence_active": "cadence: ~{days:.0f}d/release (active)",
        "int_cadence_moderate": "cadence: ~{days:.0f}d/release (moderate)",
        "int_cadence_slow": "cadence: ~{days:.0f}d/release (slow)",
        "int_semver_yes": "semver: yes",
        "int_semver_no": "semver: no",
        "int_prerelease": "pre-release",
        "int_no_release_data": "No release data",

        # ------------------------------------------------------------------
        # License interpretation
        # ------------------------------------------------------------------
        "int_lic_family_permissive": "permissive",
        "int_lic_family_copyleft": "copyleft",
        "int_lic_family_public_domain": "public domain",
        "int_lic_family_proprietary": "proprietary",
        "int_lic_family_other": "other",
        "int_lic_family_unknown": "unknown",
        "int_lic_osi_approved": "OSI-approved",
        "int_lic_none": "No license detected",

        # ------------------------------------------------------------------
        # Contributors interpretation
        # ------------------------------------------------------------------
        "int_authors": "{count} authors",
        "int_bus_factor": "bus factor: {count}",
        "int_bots": "bots: {ratio:.0%}",
        "int_lead": "lead: {login}",
        "int_historical_lead": "historical: {login}",
        "int_minor": "{count} minor",
        "int_commits_3m": "{count} commits (3m)",
        "int_commits_12m": "{count} commits (12m)",
        "int_no_activity": "no recent activity",

        # ------------------------------------------------------------------
        # Maintenance interpretation
        # ------------------------------------------------------------------
        "int_state": "state: {state}",
        "int_last_commit_today": "last commit: today",
        "int_last_commit_yesterday": "last commit: yesterday",
        "int_last_commit_days": "last commit: {days}d ago",
        "int_cpm_very_active": "{rate:.1f} commits/month (very active)",
        "int_cpm_active": "{rate:.1f} commits/month (active)",
        "int_cpm_low": "{rate:.1f} commits/month (low)",
        "int_cpm_none": "no recent commits",
        "int_issues_lt1d": "issues closed: <1d",
        "int_issues_days": "issues closed: {days:.0f}d",
        "int_issues_moderate": "issues closed: {days:.0f}d (moderate)",
        "int_issues_slow": "issues closed: {days:.0f}d (slow)",
        "int_stale_issues": "{ratio:.0%} stale issues",

        # ------------------------------------------------------------------
        # Website interpretation
        # ------------------------------------------------------------------
        "int_site_no_homepage": "No homepage declared",
        "int_site_ok": "Site reachable (HTTP {code})",
        "int_site_dns": "Domain name does not resolve: {site}",
        "int_site_timeout": "Site timed out: {site}",
        "int_site_http": "Site answers HTTP {code}: {site}",
        "int_site_redirect": "Redirect loop on the site: {site}",
        "int_site_captcha": "Site behind a bot-protection check (\"I'm not a robot\"): {site}",
        "int_site_unreachable": "Site unreachable: {site}",

        # ------------------------------------------------------------------
        # Security updates interpretation
        # ------------------------------------------------------------------
        "int_security_none": "No pending security updates",
        "int_security_pending": "{count} pending security update(s)",
        "int_security_overdue": "{count} security update(s) pending for {days} days",

        # ------------------------------------------------------------------
        # Languages interpretation
        # ------------------------------------------------------------------
        "int_primary": "primary: {language}",
        "int_language_popular": "mainstream language (top {rank})",
        "int_language_exotic": "uncommon main language ({language})",
        "int_readme_english": "README in English",
        "int_readme_not_english": "README not in English",
        "int_breakdown": "breakdown: {langs}",
        "int_ecosystem": "ecosystem: {ecosystem}",
        "int_no_language": "No language data",

        # ------------------------------------------------------------------
        # Sustainability interpretation
        # ------------------------------------------------------------------
        "int_foundation": "foundation: {name}",
        "int_funding": "funding: {platforms}",
        "int_corporate": "corporate: {company}",
        "int_governance": "governance: {model}",
        "int_no_backing": "no backing detected",
        "int_roadmap": "roadmap: {text}",
        "int_commercial": "commercial support: {text}",
        "int_security": "security: {text}",
        "int_text_state": "declared state: {state}",

        # ------------------------------------------------------------------
        # Status / state display labels
        # ------------------------------------------------------------------
        "status_healthy": "healthy",
        "status_warning": "warning",
        "status_critical": "critical",
        "status_unknown": "unknown",
        "state_active": "active",
        "state_maintenance": "maintenance",
        "state_abandoned": "abandoned",
        "state_unknown": "unknown",
        "owner_type_user": "user",
        "owner_type_organization": "organization",

        # ------------------------------------------------------------------
        # TUI dashboard
        # ------------------------------------------------------------------
        "tui_header": "GitHub Health Dashboard",
        "tui_no_description": "No description",
        "tui_stars": "Stars: {count:,}",
        "tui_forks": "Forks: {count:,}",
        "tui_created": "Created: {date}",
        "tui_owner": "Owner: {type}",
        "panel_release_health": "Release Health",
        "panel_license": "License",
        "panel_contributors": "Contributors",
        "panel_maintenance": "Maintenance",
        "panel_languages": "Languages",
        "panel_sustainability": "Sustainability",
        "panel_registries": "Package Registries",
        "panel_qualitative": "Qualitative Signals",
        "panel_website": "Website",
        "panel_security": "Security",
        "tui_mirror": "⚠ Mirror repository — development happens elsewhere",
        "tui_mirror_upstream": "⚠ Mirror of {upstream}",
        "panel_llm_recommendation": "Refined recommendation (LLM)",
        "tui_latest": "latest: {version}",
        "tui_age": "age: {days} days",
        "tui_cadence": "cadence: {days:.0f} days/release",
        "tui_semver_yes": "semver: yes",
        "tui_semver_no": "semver: no",
        "tui_prerelease": "status: pre-release",
        "tui_no_license": "No license detected",
        "tui_total": "total: {count}",
        "tui_bus_factor": "bus factor: {count}",
        "tui_bots": "bots: {ratio:.0%}",
        "tui_lead": "lead: {login}",
        "tui_historical": "historical: {login}",
        "tui_minor": "minor: {count}",
        "tui_activity_3m": "activity: {count} commits (3m)",
        "tui_activity_12m": "activity: {count} commits (12m)",
        "tui_state": "state: {state}",
        "tui_last_commit": "last commit: {days}d ago",
        "tui_frequency": "frequency: {rate:.1f} commits/month",
        "tui_issues_closed": "issues closed: {days:.0f}d",
        "tui_stale_issues": "stale issues: {ratio:.0%}",
        "tui_primary": "primary: {language}",
        "tui_ecosystem": "ecosystem: {ecosystem}",
        "tui_foundation": "foundation: {name}",
        "tui_funding": "funding: {platforms}",
        "tui_corporate": "corporate: {company}",
        "tui_governance": "governance: {model}",
        "tui_no_backing": "no backing detected",
        "tui_downloads": "downloads: {count:,}",
        "tui_recent": "recent: {count:,}",
        "tui_registry_license": "license: {license}",
        "tui_gh_license_match": "GitHub license: matches",
        "tui_gh_license_diff": "GitHub license: differs",
        "tui_deprecated": "⚠ deprecated",
        "tui_heuristic": "(name inferred from repo)",
        "tui_not_found": "(not found)",
        "tui_roadmap": "roadmap: {text}",
        "tui_commercial": "commercial support: {text}",
        "tui_security": "security: {text}",
        "tui_text_state": "declared state: {state}",
        "ui_confidence": "confidence: {conf:.0%}",

        # ------------------------------------------------------------------
        # Warnings
        # ------------------------------------------------------------------
        "warn_no_token": "No GitHub token set — anonymous requests limited (60/hour)",
        "warn_llm_unavailable": (
            "LLM configured but unreachable or invalid response — "
            "qualitative signals unavailable"
        ),
        "warn_llm_no_api_key": (
            "Remote LLM configured without an API key (GH_SCORE_LLM_API_KEY)"
        ),
        "warn_llm_contradiction": (
            "The refined recommendation (LLM) seems to contradict the "
            "extracted signals (denies: {facts})"
        ),
        "panel_warnings": "Warnings",

        # ------------------------------------------------------------------
        # Markdown report
        # ------------------------------------------------------------------
        "md_section_recommendation": "## Recommendation",
        "md_section_release_health": "## Release Health",
        "md_section_license": "## License",
        "md_section_contributors": "## Contributors",
        "md_section_maintenance": "## Maintenance",
        "md_section_languages": "## Languages",
        "md_section_sustainability": "## Sustainability",
        "md_section_qualitative": "## Qualitative Signals",
        "md_section_llm_recommendation": "## Refined recommendation (LLM)",
        "md_section_website": "## Website",
        "md_section_security": "## Security",
        "md_header_stars": "**Stars:** {count:,}",
        "md_header_forks": "**Forks:** {count:,}",
        "md_owner": "**Owner:** {type}",
        "md_latest": "**Latest:** {version}",
        "md_age": "**Age:** {days} days",
        "md_cadence": "**Cadence:** {days:.0f} days/release",
        "md_status": "**Status:** {status}",
        "md_license_label": "**License:** {spdx} ({family})",
        "md_license_none": "**License:** Not detected",
        "md_total_authors": "**Total authors:** {count}",
        "md_bus_factor": "**Bus factor:** {count}",
        "md_bot_ratio": "**Bot ratio:** {ratio:.0%}",
        "md_lead": "**Lead:** {login}",
        "md_state": "**State:** {state}",
        "md_last_commit": "**Last commit:** {days} days ago",
        "md_frequency": "**Frequency:** {rate:.1f} commits/month",
        "md_primary": "**Primary:** {language}",
        "md_breakdown": "**Breakdown:**",
        "md_foundation": "**Foundation:** {name}",
        "md_funding": "**Funding:** {platforms}",
        "md_corporate": "**Corporate backing:** {company}",
        "md_roadmap": "**Roadmap:** {text}",
        "md_commercial": "**Commercial support:** {text}",
        "md_security": "**Security:** {text}",
        "md_text_state": "**Declared state:** {state}",
        "md_confidence": "Confidence: {conf:.0%}",

        # ------------------------------------------------------------------
        # CLI console messages
        # ------------------------------------------------------------------
        "cli_analyzing": "Analyzing repository...",
        "cli_cache_cleared": "Cache cleared",
        "cli_error": "Error:",
        "cli_no_target": (
            "No URL provided and current directory is not a git repository."
        ),
        "cli_usage_1": "Usage: gh-score [URL] [OPTIONS]",
        "cli_usage_2": "Usage: gh-score analyze [URL] [OPTIONS]",
        "cli_config_title": "Current Configuration",
        "cli_config_setting": "Setting",
        "cli_config_value": "Value",
        "cli_cfg_token": "GitHub Token",
        "cli_cfg_set": "set",
        "cli_cfg_not_set": "not set",
        "cli_cfg_cache_dir": "Cache Dir",
        "cli_cfg_cache_ttl": "Cache TTL",
        "cli_cfg_llm_enabled": "LLM Enabled",
        "cli_cfg_llm_provider": "LLM Provider",
        "cli_cfg_llm_model": "LLM Model",
        "cli_cfg_llm_base_url": "LLM Base URL",
    },
}

_DEFAULT_LANG = "en"


def _lang_from_env() -> str:
    """Extract the language code (e.g. "fr") from the locale env vars."""
    raw = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
        or ""
    )
    # Handle "fr_FR.UTF-8", "fr-FR", "fr", ...
    return raw.split(".")[0].split("_")[0].split("-")[0].lower()


def current_language() -> str:
    """Return the effective language code ("fr", "en", ...).

    Unknown or unset locales fall back to English.
    """
    code = _lang_from_env()
    if code in MESSAGES:
        return code
    return _DEFAULT_LANG


def t(key: str, lang: str | None = None, **kwargs: object) -> str:
    """Translate a message key in the given language.

    Args:
        key: Stable message identifier from the catalog.
        lang: Language code; defaults to the env-derived language.
        **kwargs: Values for str.format placeholders in the message.

    Returns:
        The translated, formatted message.  Unknown keys fall back to
        English, then to the raw key.
    """
    if lang is None:
        lang = current_language()
    catalog = MESSAGES.get(lang, MESSAGES[_DEFAULT_LANG])
    template = catalog.get(key, MESSAGES[_DEFAULT_LANG].get(key, key))
    return template.format(**kwargs)
