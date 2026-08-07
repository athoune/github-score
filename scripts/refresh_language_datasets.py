#!/usr/bin/env python3
"""Refresh the committed language-popularity datasets.

Two sources are aggregated into small CSVs committed under
``src/gh_score/data/`` (and shipped in the wheel):

- ``pypl_languages.csv`` — top-N of the PYPL index
  (https://pypl.github.io/PYPL.html): ``rank, language, share``.
- ``github_languages.csv`` — top-N languages by number of pushers in the
  most recent quarter of the GitHub Innovation Graph
  (https://github.com/github/innovationgraph): ``rank, language, num_pushers``.

The analyzers use these files to decide whether a project's main language
is mainstream or exotic.

Usage::

    python scripts/refresh_language_datasets.py [--top 20]
"""

from __future__ import annotations

import argparse
import csv
import html.parser
import io
import sys
from pathlib import Path

import httpx

PYPL_URL = "https://pypl.github.io/PYPL.html"
GITHUB_URL = (
    "https://raw.githubusercontent.com/github/innovationgraph/main/data/languages.csv"
)
DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "gh_score" / "data"

_UA = "gh-score/0.1.0 (dataset refresh)"


class _TableParser(html.parser.HTMLParser):
    """Collect the text of every ``<tr>`` as a list of cell strings."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._cells: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "tr":
            self._cells = []
        elif tag == "td":
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._current is not None:
            self._cells.append("".join(self._current).strip())
            self._current = None
        elif tag == "tr" and self._cells:
            self.rows.append(self._cells)


def parse_pypl_table(html_text: str) -> list[tuple[int, str, float]]:
    """Return ``[(rank, language, share)]`` from the PYPL "All" ranking.

    The table is rendered client-side: the rows live inside a JavaScript
    string literal (``table = "…\\<td>…</tr>\\…"``), one section per region
    ("All", "US", "DE", …). The "All" fragment is extracted, un-escaped
    and parsed as HTML. Header rows (``<th>``) are ignored.
    """
    marker = "<!-- begin section All-->"
    start = html_text.find(marker)
    if start < 0:
        return []
    next_section = html_text.find("<!-- begin section ", start + len(marker))
    fragment = html_text[start:] if next_section < 0 else html_text[start:next_section]
    # Un-escape the JS string literal: line continuations and \" quotes.
    fragment = fragment.replace("\\\r\n", "").replace("\\\n", "").replace('\\"', '"')

    parser = _TableParser()
    # The first row of the fragment lacks its opening <tr>.
    parser.feed("<tr>" + fragment)

    result: list[tuple[int, str, float]] = []
    for cells in parser.rows:
        if len(cells) < 4:
            continue
        if not cells[0].isdigit() or not cells[3].strip().endswith("%"):
            continue
        try:
            share = float(cells[3].replace("%", "").strip())
        except ValueError:
            continue
        result.append((int(cells[0]), cells[2].strip(), share))
    return result


def aggregate_github_languages(csv_text: str) -> list[tuple[str, int]]:
    """Sum pushers per language over the most recent quarter, desc."""
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        return []
    latest = max((r["year"], r["quarter"]) for r in rows)
    totals: dict[str, int] = {}
    for r in rows:
        if (r["year"], r["quarter"]) != latest:
            continue
        try:
            totals[r["language"]] = totals.get(r["language"], 0) + int(r["num_pushers"])
        except (ValueError, KeyError):
            continue
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)


def write_pypl(rows: list[tuple[int, str, float]], top: int) -> Path:
    path = DATA_DIR / "pypl_languages.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "language", "share"])
        for rank, name, share in rows[:top]:
            writer.writerow([rank, name, share])
    return path


def write_github(rows: list[tuple[str, int]], top: int) -> Path:
    path = DATA_DIR / "github_languages.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "language", "num_pushers"])
        for rank, (name, pushers) in enumerate(rows[:top], start=1):
            writer.writerow([rank, name, pushers])
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=20, help="rows kept per dataset")
    args = parser.parse_args(argv)

    with httpx.Client(
        follow_redirects=True, timeout=60.0, headers={"User-Agent": _UA}
    ) as client:
        pypl_text = client.get(PYPL_URL).text
        github_text = client.get(GITHUB_URL).text

    pypl_rows = parse_pypl_table(pypl_text)
    github_rows = aggregate_github_languages(github_text)

    if not pypl_rows or not github_rows:
        print("Failed to parse one of the sources; nothing written.", file=sys.stderr)
        return 1

    pypl_path = write_pypl(pypl_rows, args.top)
    github_path = write_github(github_rows, args.top)
    print(f"Wrote {pypl_path} ({min(len(pypl_rows), args.top)} rows)")
    print(f"Wrote {github_path} ({min(len(github_rows), args.top)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
