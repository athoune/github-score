"""Lightweight internationalization for user-facing strings.

The active language is derived from the environment using the standard
locale precedence: ``LC_ALL`` > ``LC_MESSAGES`` > ``LANG``.  Locale
strings like ``fr_FR.UTF-8`` or ``fr-FR`` are reduced to their language
code (``fr``).  Unset or unknown locales fall back to English.

This is intentionally dependency-free: no gettext catalogs, no build
step.  Messages live in a plain dict keyed by stable identifiers, with
one catalog per supported language.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Message catalog
# ---------------------------------------------------------------------------
# Keys are stable identifiers used by the code.  Values may contain
# str.format placeholders, e.g. "{months}".

MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        # Recommendation verdicts
        "rec_archived": "Projet archivé — plus aucun développement",
        "rec_disabled": "Projet désactivé",
        "rec_deprecated": "Projet déprécié sur le registre",
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

        # Reasoning lines
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

        # Objective facts appended to the reasoning
        "fact_stars": "{stars:,} étoiles",
        "fact_authors": "{authors} auteurs",

        # UI labels
        "ui_confidence": "confiance: {conf:.0%}",
        "md_confidence": "Confiance: {conf:.0%}",
    },
    "en": {
        # Recommendation verdicts
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

        # Reasoning lines
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

        # Objective facts appended to the reasoning
        "fact_stars": "{stars:,} stars",
        "fact_authors": "{authors} authors",

        # UI labels
        "ui_confidence": "confidence: {conf:.0%}",
        "md_confidence": "Confidence: {conf:.0%}",
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
