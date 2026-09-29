"""Analyzers package.

All indicator family analyzers.
"""

from gh_score.core.analyzers.contributors import analyze_contributors
from gh_score.core.analyzers.languages import analyze_languages
from gh_score.core.analyzers.license_analyzer import analyze_license
from gh_score.core.analyzers.maintenance import analyze_maintenance
from gh_score.core.analyzers.qualitative import analyze_qualitative
from gh_score.core.analyzers.recommendation import analyze_recommendation
from gh_score.core.analyzers.release_health import analyze_release_health
from gh_score.core.analyzers.security import analyze_security
from gh_score.core.analyzers.sustainability import analyze_sustainability
from gh_score.core.analyzers.website import analyze_website

__all__ = [
    "analyze_contributors",
    "analyze_languages",
    "analyze_license",
    "analyze_maintenance",
    "analyze_qualitative",
    "analyze_recommendation",
    "analyze_release_health",
    "analyze_security",
    "analyze_sustainability",
    "analyze_website",
]
