"""Human-readable duration rendering.

Two forms share one set of thresholds, so a cell and a sentence never
disagree about the same age:

- :func:`humanize_days` — compact, for table cells: ``3d``, ``8w``, ``7mo``,
  ``3y``;
- :func:`humanize_days_long` — prose, for sentences: ``3 days``, ``8 weeks``,
  ``7 months``, ``3 years``.

Precision is deliberately coarse. A health report answers "is this project
alive?", not "when exactly" — past two months the day count is noise, and
``2549d`` is unreadable where ``7y`` is instant.

Compact units stay language-neutral (``d`` / ``w`` / ``mo`` / ``y``): they
are analytical cells, usually monospaced and compared column to column. Only
"today" is translated, since it is a word rather than a unit.
"""

from __future__ import annotations

from gh_score.i18n import t

_DAYS_PER_WEEK = 7
# Average Gregorian month and year (365.25 / 12), used for the division only.
# The unit *boundaries* below stay plain integers, so the average year length
# can never make a one-year-old project render as "12mo".
_DAYS_PER_MONTH = 30.4375
_DAYS_PER_YEAR = 365.25

# Unit identifiers, also the suffix of the i18n plural keys.
_DAY = "day"
_WEEK = "week"
_MONTH = "month"
_YEAR = "year"

_COMPACT_UNITS = {_DAY: "d", _WEEK: "w", _MONTH: "mo", _YEAR: "y"}

# Reporting a duration as a plain day count stays truthful (and readable) up
# to two weeks; past that the number stops meaning anything on its own. The
# month/year boundary is a plain 365 days so that a one-year-old project does
# not render as "12mo" because the average year is 365.25 days.
_MAX_DAY_COUNT = 14
_MAX_WEEK_COUNT = 60
_MAX_MONTH_COUNT = 365


def _round_half_up(value: float) -> int:
    """Round at .5 away from zero.

    ``round()`` is banker's rounding (1.5 → 2 but 2.5 → 2), which would make
    the rendered unit jump around for no reason the reader can see.
    """
    return int(value + 0.5)


def _split(days: int) -> tuple[int, str]:
    """Split a day count into a rounded count and its unit."""
    if days < _MAX_DAY_COUNT:
        return days, _DAY
    if days < _MAX_WEEK_COUNT:
        return _round_half_up(days / _DAYS_PER_WEEK), _WEEK
    if days < _MAX_MONTH_COUNT:
        return _round_half_up(days / _DAYS_PER_MONTH), _MONTH
    return _round_half_up(days / _DAYS_PER_YEAR), _YEAR


def humanize_days(days: int, lang: str | None = None) -> str:
    """Compact duration for a table cell: ``3`` → ``3d``, ``2549`` → ``7y``."""
    if days <= 0:
        return t("cmp_table_today", lang=lang)
    count, unit = _split(days)
    return f"{count}{_COMPACT_UNITS[unit]}"


def humanize_days_long(days: int, lang: str | None = None) -> str:
    """Prose duration: ``10`` → ``10 days``, ``2549`` → ``7 years``."""
    count, unit = _split(days)
    plural = "many" if count != 1 else "one"
    return f"{count} {t(f'dur_{unit}_{plural}', lang=lang)}"


def format_duration(days: int | None, lang: str | None = None) -> str:
    """Table cell value: an em dash when the age is unknown."""
    if days is None:
        return "—"
    return humanize_days(days, lang=lang)
