"""Tests for analyzers."""

from datetime import datetime, timedelta, timezone

from gh_score.core.models import (
    Repository,
    RepoUrl,
    Release,
    ReleaseHealth,
    LicenseInfo,
    LicenseFamily,
    Contributor,
    ContributorStats,
    Commit,
    LanguageBreakdown,
    CommunityFiles,
    SecurityUpdate,
    Status,
    MaintenanceState,
)
from gh_score.core.analyzers import (
    analyze_release_health,
    analyze_license,
    analyze_contributors,
    analyze_maintenance,
    analyze_languages,
    analyze_security,
    analyze_sustainability,
)


class TestReleaseHealthAnalyzer:
    def test_no_releases(self):
        repo = Repository(url=RepoUrl("owner", "repo"))
        result = analyze_release_health(repo)
        assert result.status == Status.CRITICAL
        assert result.latest_version is None

    def test_recent_release(self):
        now = datetime.now(timezone.utc)
        releases = [
            Release(
                tag_name="v1.0.0",
                published_at=now - timedelta(days=10),
            )
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            release_health=ReleaseHealth(releases=releases),
        )
        result = analyze_release_health(repo)
        assert result.latest_version == "v1.0.0"
        assert result.age_days == 10
        assert result.status == Status.HEALTHY

    def test_old_release(self):
        now = datetime.now(timezone.utc)
        releases = [
            Release(
                tag_name="v1.0.0",
                published_at=now - timedelta(days=400),
            )
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            release_health=ReleaseHealth(releases=releases),
        )
        result = analyze_release_health(repo)
        assert result.age_days == 400
        assert result.status == Status.CRITICAL

    def test_semver_compliance(self):
        releases = [
            Release(tag_name="v1.0.0", published_at=datetime.now(timezone.utc)),
            Release(tag_name="v1.1.0", published_at=datetime.now(timezone.utc)),
            Release(tag_name="v2.0.0", published_at=datetime.now(timezone.utc)),
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            release_health=ReleaseHealth(releases=releases),
        )
        result = analyze_release_health(repo)
        assert result.semver_compliant is True

    def test_prerelease(self):
        releases = [
            Release(
                tag_name="v2.0.0-beta",
                published_at=datetime.now(timezone.utc),
                prerelease=True,
            )
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            release_health=ReleaseHealth(releases=releases),
        )
        result = analyze_release_health(repo)
        assert result.is_prerelease is True
        assert result.status == Status.WARNING


class TestLicenseAnalyzer:
    def test_mit_license(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            license=LicenseInfo(
                spdx_id="MIT",
                family=LicenseFamily.PERMISSIVE,
                osi_approved=True,
            ),
        )
        result = analyze_license(repo)
        assert result.spdx_id == "MIT"
        assert result.status == Status.HEALTHY

    def test_no_license(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            license=LicenseInfo(),
        )
        result = analyze_license(repo)
        assert result.status == Status.CRITICAL

    def test_gpl_license(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            license=LicenseInfo(
                spdx_id="GPL-3.0",
                family=LicenseFamily.COPYLEFT,
                osi_approved=True,
            ),
        )
        result = analyze_license(repo)
        assert result.status == Status.HEALTHY


class TestContributorsAnalyzer:
    def test_single_contributor(self):
        contributors = [
            Contributor(login="alice", commits=100),
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            contributors=ContributorStats(contributors=contributors, total_commit_count=100),
        )
        result = analyze_contributors(repo)
        assert result.total_authors == 1
        assert result.bus_factor == 1
        assert result.status == Status.CRITICAL

    def test_multiple_contributors(self):
        contributors = [
            Contributor(login="alice", commits=40),
            Contributor(login="bob", commits=35),
            Contributor(login="charlie", commits=25),
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            contributors=ContributorStats(contributors=contributors, total_commit_count=100),
        )
        result = analyze_contributors(repo)
        assert result.total_authors == 3
        # Alice (40) + Bob (35) = 75% > 50%, so bus_factor = 2
        assert result.bus_factor == 2

    def test_bot_detection(self):
        contributors = [
            Contributor(login="alice", commits=80),
            Contributor(login="dependabot[bot]", commits=20, is_bot=True),
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            contributors=ContributorStats(contributors=contributors, total_commit_count=100),
        )
        result = analyze_contributors(repo)
        assert result.total_authors == 1  # Only alice
        assert result.bot_ratio == 0.2


class TestMaintenanceAnalyzer:
    def test_active_maintenance(self):
        now = datetime.now(timezone.utc)
        # Create enough commits to be considered active (>= 2 commits/month)
        # Over 12 months, we need at least 24 commits
        commits = []
        for i in range(30):
            commits.append(Commit(sha=f"commit{i}", author_date=now - timedelta(days=i)))
        
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            commits=commits,
        )
        result = analyze_maintenance(repo)
        assert result.state == MaintenanceState.ACTIVE
        assert result.last_commit_days_ago is not None
        assert result.last_commit_days_ago <= 5

    def test_abandoned(self):
        now = datetime.now(timezone.utc)
        commits = [
            Commit(sha="abc", author_date=now - timedelta(days=200)),
        ]
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            commits=commits,
        )
        result = analyze_maintenance(repo)
        assert result.state == MaintenanceState.ABANDONED
        assert result.status == Status.CRITICAL

    def test_no_commits(self):
        repo = Repository(url=RepoUrl("owner", "repo"))
        result = analyze_maintenance(repo)
        assert result.state == MaintenanceState.UNKNOWN


class TestLanguagesAnalyzer:
    def test_primary_language(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            languages=LanguageBreakdown(languages={"Python": 1000, "JavaScript": 500}),
        )
        result = analyze_languages(repo)
        assert result.primary == "Python"
        assert "Python" in result.breakdown
        assert result.ecosystem == "python"

    def test_javascript_ecosystem(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            languages=LanguageBreakdown(languages={"JavaScript": 1000, "HTML": 200}),
        )
        result = analyze_languages(repo)
        assert result.ecosystem == "javascript"


class TestLanguagePopularity:
    """The main-language popularity check against the committed datasets."""

    @staticmethod
    def _rankings():
        return {"python": (1, "pypl"), "c": (3, "pypl"), "c++": (3, "pypl")}

    def _repo(self, primary: str) -> Repository:
        return Repository(
            url=RepoUrl("owner", "repo"),
            languages=LanguageBreakdown(languages={primary: 1000}),
        )

    def test_mainstream_language(self, monkeypatch):
        monkeypatch.setattr(
            "gh_score.core.analyzers.languages._load_rankings", self._rankings
        )
        result = analyze_languages(self._repo("Python"))
        assert result.is_exotic is False
        assert result.popularity_rank == 1
        assert result.popularity_source == "pypl"

    def test_alias_variant_is_mainstream(self, monkeypatch):
        # PYPL ranks "C/C++"; Linguist reports "C++".
        monkeypatch.setattr(
            "gh_score.core.analyzers.languages._load_rankings", self._rankings
        )
        result = analyze_languages(self._repo("C++"))
        assert result.is_exotic is False
        assert result.popularity_rank == 3

    def test_exotic_language(self, monkeypatch):
        monkeypatch.setattr(
            "gh_score.core.analyzers.languages._load_rankings", self._rankings
        )
        result = analyze_languages(self._repo("LOLCODE"))
        assert result.is_exotic is True
        assert result.popularity_rank is None

    def test_no_primary(self, monkeypatch):
        monkeypatch.setattr(
            "gh_score.core.analyzers.languages._load_rankings", self._rankings
        )
        result = analyze_languages(Repository(url=RepoUrl("o", "r")))
        assert result.is_exotic is None

    def test_datasets_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "gh_score.core.analyzers.languages._load_rankings", lambda: None
        )
        result = analyze_languages(self._repo("Python"))
        assert result.is_exotic is None
        assert result.popularity_rank is None


class TestSustainabilityAnalyzer:
    def test_no_backing(self):
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            community=CommunityFiles(),
        )
        result = analyze_sustainability(repo)
        assert result.status == Status.WARNING
        assert not result.has_funding

    def test_with_funding(self):
        community = CommunityFiles()
        community.has_funding = True  # type: ignore
        community.funding = {"github": ["alice"]}
        repo = Repository(
            url=RepoUrl("owner", "repo"),
            community=community,
        )
        result = analyze_sustainability(repo)
        assert result.has_funding is True
        assert "GitHub Sponsors" in result.funding_platforms


class TestSecurityAnalyzer:
    """Pending Dependabot security updates: recent = warning, old = critical."""

    def _repo(self, *days_ago: int) -> Repository:
        repo = Repository(url=RepoUrl("owner", "repo"))
        now = datetime.now(timezone.utc)
        for i, days in enumerate(days_ago, start=1):
            repo.security_updates.append(
                SecurityUpdate(
                    number=i,
                    title=f"Bump package {i}",
                    created_at=now - timedelta(days=days),
                )
            )
        return repo

    def test_none_healthy(self):
        ind = analyze_security(self._repo())
        assert ind.status == Status.HEALTHY
        assert ind.pending_count == 0
        assert ind.oldest_days is None

    def test_recent_pending_warning(self):
        ind = analyze_security(self._repo(2))
        assert ind.status == Status.WARNING
        assert ind.pending_count == 1
        assert ind.oldest_days == 2

    def test_overdue_critical(self):
        ind = analyze_security(self._repo(10))
        assert ind.status == Status.CRITICAL
        assert ind.oldest_days == 10

    def test_oldest_drives_status(self):
        # One fresh update (1 day) + one overdue (5 days) → critical.
        ind = analyze_security(self._repo(1, 5))
        assert ind.status == Status.CRITICAL
        assert ind.oldest_days == 5
        assert ind.pending_count == 2
