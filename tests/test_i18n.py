"""Tests for the lightweight internationalization module."""

from datetime import datetime, timedelta, timezone

from gh_score.core.analyzers import (
    analyze_contributors,
    analyze_maintenance,
    analyze_release_health,
)
from gh_score.core.analyzers.license_analyzer import license_family_label
from gh_score.core.analyzers.recommendation import analyze_recommendation
from gh_score.core.models import (
    AnalysisResult,
    Commit,
    Contributor,
    ContributorStats,
    ContributorsIndicator,
    LicenseFamily,
    LicenseIndicator,
    MaintenanceIndicator,
    MaintenanceState,
    Release,
    ReleaseHealth,
    ReleaseHealthIndicator,
    RepoUrl,
    Repository,
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


class TestAnalyzerInterpretations:
    """Analyzer interpretation strings follow the selected language."""

    def test_contributors_interpretation_fr(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            contributors=ContributorStats(
                contributors=[
                    Contributor(login="alice", commits=100),
                    Contributor(login="dependabot[bot]", commits=20, is_bot=True),
                ],
                total_commit_count=120,
            ),
        )
        ind = analyze_contributors(repo, lang="fr")
        # The interpretation must be built from the catalog; compare with t()
        # so wording tweaks (e.g. translation improvements) don't break tests.
        assert t("int_authors", lang="fr", count=1) in ind.interpretation
        assert t("int_bus_factor", lang="fr", count=1) in ind.interpretation
        assert t("int_bots", lang="fr", ratio=20 / 120) in ind.interpretation

    def test_contributors_interpretation_en(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            contributors=ContributorStats(
                contributors=[Contributor(login="alice", commits=100)],
                total_commit_count=100,
            ),
        )
        ind = analyze_contributors(repo, lang="en")
        assert t("int_authors", lang="en", count=1) in ind.interpretation
        assert t("int_bus_factor", lang="en", count=1) in ind.interpretation

    def test_maintenance_interpretation_fr(self):
        now = datetime.now(timezone.utc)
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            commits=[
                Commit(sha="a", author_date=now - timedelta(days=30)),
                Commit(sha="b", author_date=now - timedelta(days=5)),
            ],
        )
        ind = analyze_maintenance(repo, lang="fr")
        assert "état :" in ind.interpretation
        assert "dernier commit :" in ind.interpretation

    def test_maintenance_interpretation_en(self):
        now = datetime.now(timezone.utc)
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            commits=[Commit(sha="a", author_date=now - timedelta(days=30))],
        )
        ind = analyze_maintenance(repo, lang="en")
        assert "state:" in ind.interpretation
        assert "last commit:" in ind.interpretation

    def test_release_health_interpretation_fr(self):
        now = datetime.now(timezone.utc)
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            release_health=ReleaseHealth(
                releases=[
                    Release(tag_name="v1.0.0", published_at=now - timedelta(days=10)),
                ]
            ),
        )
        ind = analyze_release_health(repo, lang="fr")
        assert "Dernière : v1.0.0" in ind.interpretation
        assert "publiée il y a 10 jours" in ind.interpretation

    def test_release_health_interpretation_en(self):
        now = datetime.now(timezone.utc)
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            release_health=ReleaseHealth(
                releases=[
                    Release(tag_name="v1.0.0", published_at=now - timedelta(days=10)),
                ]
            ),
        )
        ind = analyze_release_health(repo, lang="en")
        assert "Latest: v1.0.0" in ind.interpretation
        assert "released 10 days ago" in ind.interpretation

    def test_license_family_label(self):
        assert license_family_label(LicenseFamily.PERMISSIVE, lang="fr") == "permissive"
        assert license_family_label(LicenseFamily.PUBLIC_DOMAIN, lang="fr") == "domaine public"
        assert license_family_label(LicenseFamily.PROPRIETARY, lang="en") == "proprietary"
        assert license_family_label(LicenseFamily.OTHER, lang="en") == "other"


class TestCliStrings:
    """CLI console messages are localized."""

    def test_analyzing_fr(self):
        assert t("cli_analyzing", lang="fr") == "Analyse du dépôt..."

    def test_analyzing_en(self):
        assert t("cli_analyzing", lang="en") == "Analyzing repository..."

    def test_config_title_fr(self):
        assert t("cli_config_title", lang="fr") == "Configuration actuelle"

    def test_config_title_en(self):
        assert t("cli_config_title", lang="en") == "Current Configuration"

    def test_error_label(self):
        assert t("cli_error", lang="fr") == "Erreur :"
        assert t("cli_error", lang="en") == "Error:"

    def test_status_labels(self):
        assert t("status_healthy", lang="fr") == "sain"
        assert t("status_warning", lang="en") == "warning"
        assert t("state_abandoned", lang="fr") == "abandonné"
        assert t("state_abandoned", lang="en") == "abandoned"


class TestQualitativeKeys:
    """Qualitative (LLM) keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in (
                "rec_text_discontinued", "reason_text_discontinued",
                "reason_text_active",
                "fact_roadmap", "fact_commercial", "fact_security",
                "int_roadmap", "int_commercial", "int_security", "int_text_state",
                "panel_qualitative",
                "tui_roadmap", "tui_security", "tui_commercial", "tui_text_state",
                "md_section_qualitative", "md_roadmap", "md_security",
                "md_commercial", "md_text_state",
            ):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"


class TestRefinedRecommendationKeys:
    """Refined LLM recommendation keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in ("panel_llm_recommendation", "md_section_llm_recommendation"):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"


class TestWebsiteKeys:
    """Website indicator keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in (
                "panel_website", "md_section_website",
                "int_site_no_homepage", "int_site_ok", "int_site_dns",
                "int_site_timeout", "int_site_http", "int_site_redirect",
                "int_site_captcha", "int_site_unreachable",
                "rec_site_down", "rec_site_degraded",
                "reason_site_down", "reason_site_dns", "reason_site_http",
                "reason_site_redirect", "reason_site_timeout",
                "reason_site_captcha",
            ):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"

    def test_interpretation_fr(self):
        from gh_score.core.analyzers.website import analyze_website
        from gh_score.core.models import WebsiteError, WebsiteInfo

        ind = analyze_website(
            WebsiteInfo(url="https://example.com", error=WebsiteError.DNS),
            lang="fr",
        )
        assert t("int_site_dns", lang="fr", site="https://example.com") in ind.interpretation

    def test_interpretation_en(self):
        from gh_score.core.analyzers.website import analyze_website
        from gh_score.core.models import WebsiteInfo

        ind = analyze_website(WebsiteInfo(url="https://example.com", status_code=200), lang="en")
        assert t("int_site_ok", lang="en", site="https://example.com", code=200) in ind.interpretation


class TestLanguagePopularityKeys:
    """Language popularity keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in (
                "int_language_popular",
                "int_language_exotic",
                "rec_language_exotic",
                "reason_language_exotic",
            ):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"


class TestContradictionGuardKeys:
    """Contradiction-guard keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in (
                "warn_llm_contradiction",
                "fact_funding",
                "fact_corporate",
                "fact_foundation",
            ):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"


class TestSecurityKeys:
    """Security indicator keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in (
                "panel_security", "md_section_security",
                "int_security_none", "int_security_pending", "int_security_overdue",
                "rec_security_pending", "rec_security_overdue",
                "reason_security_pending", "reason_security_overdue",
            ):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"


class TestReadmeLanguageKeys:
    """README language interpretation keys exist in both catalogs."""

    def test_keys_present(self):
        from gh_score.i18n import MESSAGES

        for lang in ("fr", "en"):
            for key in ("int_readme_english", "int_readme_not_english"):
                assert key in MESSAGES[lang], f"{lang}:{key} missing"
