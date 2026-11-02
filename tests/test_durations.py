"""Tests for the human-readable duration rendering."""

from __future__ import annotations

import pytest

from gh_score.durations import (
    format_duration,
    humanize_days,
    humanize_days_long,
)


class TestCompactForm:
    """Table cells: short, language-neutral units."""

    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (1, "1d"),
            (13, "13d"),
            (14, "2w"),
            (59, "8w"),
            (60, "2mo"),
            (203, "7mo"),
            (364, "12mo"),
            (365, "1y"),
            (1196, "3y"),
            (2549, "7y"),
        ],
    )
    def test_units(self, days, expected):
        assert humanize_days(days) == expected

    def test_zero_is_today(self):
        assert humanize_days(0, lang="en") == "today"
        assert humanize_days(0, lang="fr") == "aujourd'hui"

    def test_negative_is_today(self):
        # Clock skew (a commit dated in the future) must not render "-3d".
        assert humanize_days(-3, lang="en") == "today"

    def test_never_renders_a_zero_count(self):
        for days in range(14, 4000):
            count = humanize_days(days)
            assert not count.startswith("0"), days


class TestLongForm:
    """Prose: full words, pluralized."""

    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (1, "1 day"),
            (10, "10 days"),
            (14, "2 weeks"),
            (30, "4 weeks"),
            (203, "7 months"),
            (365, "1 year"),
            (2549, "7 years"),
        ],
    )
    def test_english(self, days, expected):
        assert humanize_days_long(days, lang="en") == expected

    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (1, "1 jour"),
            (10, "10 jours"),
            (14, "2 semaines"),
            (30, "4 semaines"),
            (2549, "7 ans"),
            (365, "1 an"),
        ],
    )
    def test_french(self, days, expected):
        assert humanize_days_long(days, lang="fr") == expected


class TestCellValue:
    def test_unknown_age_is_an_em_dash(self):
        assert format_duration(None) == "—"

    def test_known_age_uses_the_compact_form(self):
        assert format_duration(2549) == "7y"
