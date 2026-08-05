"""Tests for the lightweight internationalization module."""

from datetime import datetime, timedelta, timezone

from gh_score.core.analyzers.recommendation import analyze_recommendation
from gh_score.core.models import (
    AnalysisResult,
    ContributorsIndicator,
    LicenseIndicator,
    MaintenanceIndicator,
    MaintenanceState,
    ReleaseHealthIndicator,
    RepoUrl,
    RepositoryMeta,
    SustainabilityIndicator,
)
from gh_score.i18n import current_language, t


class TestCurrentLanguage:
    def test_fr_lang(self, monkeypatch):
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        assert current_language() == "fr"

    def test_en_lang(self, monkeypatch):
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        assert current_language() == "en"

    def test_hyphen_format(self, monkeypatch):
        monkeypatch.setenv("LANG", "fr-FR")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        assert current_language() == "fr"

    def test_unknown_locale_falls_back_to_english(self, monkeypatch):
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        assert current_language() == "en"

    def test_unset_lang_falls_back_to_english(self, monkeypatch):
        monkeypatch.delenv("LANG", raising=False)
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        assert current_language() == "en"

    def test_lc_all_overrides_lang(self, monkeypatch):
        monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_MESSAGES", raising=False)
        assert current_language() == "fr"

    def test_lc_messages_overrides_lang(self, monkeypatch):
        monkeypatch.setenv("LC_MESSAGES", "fr_FR.UTF-8")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        assert current_language() == "fr"


class TestTranslate:
    def test_french(self):
        assert t("rec_active", lang="fr") == "Projet actif"

    def test_english(self):
        assert t("rec_active", lang="en") == "Active project"

    def test_format_params(self):
        assert (
            t("rec_abandoned_months", lang="fr", months=7)
            == "Projet abandonné — pas de commit depuis 7 mois"
        )

    def test_format_stars_thousands(self):
        assert t("fact_stars", lang="fr", stars=1234) == "1,234 étoiles"

    def test_format_ratio(self):
        assert t("reason_bots", lang="fr", ratio=0.9) == "90% des commits proviennent de bots"

    def test_unknown_lang_falls_back_to_english(self):
        assert t("rec_active", lang="xx") == "Active project"

    def test_unknown_key_returns_key(self):
        assert t("no_such_key", lang="en") == "no_such_key"

    def test_env_language_used_by_default(self, monkeypatch):
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        assert t("rec_active") == "Projet actif"


class TestRecommendationLanguage:
    def _make_active_result(self) -> AnalysisResult:
        now = datetime.now(timezone.utc)
        return AnalysisResult(
            url=RepoUrl("owner", "repo"),
            meta=RepositoryMeta(stars=200, created_at=now - timedelta(days=365)),
            release_health=ReleaseHealthIndicator(latest_version="v1.0.0"),
            license=LicenseIndicator(),
            contributors=ContributorsIndicator(total_authors=3),
            maintenance=MaintenanceIndicator(state=MaintenanceState.ACTIVE),
            languages=None,  # type: ignore[arg-type]  # not read by recommendation
            sustainability=SustainabilityIndicator(),
        )

    def test_french_message(self):
        result = self._make_active_result()
        rec = analyze_recommendation(result, lang="fr")
        assert rec.message == "Projet actif"

    def test_english_message(self):
        result = self._make_active_result()
        rec = analyze_recommendation(result, lang="en")
        assert rec.message == "Active project"

    def test_env_language_applies_to_analysis(self, monkeypatch):
        result = self._make_active_result()
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        assert analyze_recommendation(result).message == "Projet actif"

    def test_facts_are_translated(self):
        result = self._make_active_result()
        rec = analyze_recommendation(result, lang="en")
        assert any("200 stars" in r for r in rec.reasoning)
        assert any("3 authors" in r for r in rec.reasoning)
