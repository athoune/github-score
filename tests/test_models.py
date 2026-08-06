"""Tests for core models."""

import pytest
from datetime import datetime, timezone

from gh_score.core.models import (
    QualitativeIndicator,
    QualitativeSignals,
    RepoUrl,
    Release,
    ReleaseHealth,
    LanguageBreakdown,
    Status,
)


class TestQualitativeSignals:
    def test_empty_is_not_available(self):
        assert QualitativeSignals().any is False

    def test_any_signal_marks_available(self):
        s = QualitativeSignals(roadmap="v2 planned")
        assert s.any is True
        assert QualitativeSignals(text_maintenance_state="abandoned").any is True

    def test_indicator_defaults(self):
        ind = QualitativeIndicator()
        assert ind.available is False
        assert ind.status == Status.UNKNOWN
        assert ind.roadmap is None



class TestRepoUrl:
    def test_parse_https(self):
        url = RepoUrl.parse("https://github.com/owner/repo")
        assert url.owner == "owner"
        assert url.repo == "repo"

    def test_parse_https_with_git(self):
        url = RepoUrl.parse("https://github.com/owner/repo.git")
        assert url.owner == "owner"
        assert url.repo == "repo"

    def test_parse_https_with_www(self):
        url = RepoUrl.parse("https://www.github.com/owner/repo")
        assert url.owner == "owner"
        assert url.repo == "repo"

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            RepoUrl.parse("not-a-url")

    def test_parse_invalid_domain(self):
        with pytest.raises(ValueError):
            RepoUrl.parse("https://gitlab.com/owner/repo")

    def test_api_url(self):
        url = RepoUrl(owner="owner", repo="repo")
        assert url.api_url == "https://api.github.com/repos/owner/repo"

    def test_html_url(self):
        url = RepoUrl(owner="owner", repo="repo")
        assert url.html_url == "https://github.com/owner/repo"


class TestReleaseHealth:
    def test_latest_release(self):
        releases = [
            Release(tag_name="v1.0.0", published_at=datetime(2023, 1, 1, tzinfo=timezone.utc)),
            Release(tag_name="v2.0.0", published_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            Release(tag_name="v1.5.0", published_at=datetime(2023, 6, 1, tzinfo=timezone.utc)),
        ]
        rh = ReleaseHealth(releases=releases)
        latest = rh.latest
        assert latest is not None
        assert latest.tag_name == "v2.0.0"

    def test_latest_release_excludes_drafts(self):
        releases = [
            Release(tag_name="v1.0.0", published_at=datetime(2023, 1, 1, tzinfo=timezone.utc)),
            Release(tag_name="v2.0.0-draft", published_at=datetime(2024, 1, 1, tzinfo=timezone.utc), draft=True),
        ]
        rh = ReleaseHealth(releases=releases)
        latest = rh.latest
        assert latest is not None
        assert latest.tag_name == "v1.0.0"

    def test_no_releases(self):
        rh = ReleaseHealth(releases=[])
        assert rh.latest is None


class TestLanguageBreakdown:
    def test_primary_language(self):
        lb = LanguageBreakdown(languages={"Python": 1000, "JavaScript": 500, "HTML": 200})
        assert lb.primary == "Python"

    def test_total_bytes(self):
        lb = LanguageBreakdown(languages={"Python": 1000, "JavaScript": 500})
        assert lb.total_bytes == 1500

    def test_percentages(self):
        lb = LanguageBreakdown(languages={"Python": 750, "JavaScript": 250})
        pcts = lb.percentages()
        assert pcts["Python"] == 75.0
        assert pcts["JavaScript"] == 25.0

    def test_empty(self):
        lb = LanguageBreakdown(languages={})
        assert lb.primary is None
        assert lb.total_bytes == 0
        assert lb.percentages() == {}
