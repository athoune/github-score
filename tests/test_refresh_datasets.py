"""Tests for the language-dataset refresh script helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_language_datasets.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("refresh_language_datasets", _SCRIPT)
    assert spec and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The PYPL page renders its table inside a JavaScript string literal, with
# backslash line continuations and escaped quotes.
_PYPL_HTML = """\
<html><script>
    table = "<!-- begin section All-->\\
<td class=center>1</td><td class=center></td><td>Python</td><td class=right>50.44 %</td><td class=\\"right optCol\\">+20.2 %</td></tr>\\
<tr><td class=center>2</td><td class=center><image src=Up.png></td><td>Java</td><td class=right>12.8 %</td><td class=\\"right optCol\\">-2.0 %</td></tr>\\
<tr><td class=center>3</td><td class=center></td><td>C/C++</td><td class=right>8.57 %</td></tr>\\
<!-- begin section US-->\\
<td class=center>1</td><td>Python</td><td class=right>53.47 %</td></tr>"
</script></html>
"""

_GITHUB_CSV = """\
num_pushers,language,language_type,iso2_code,year,quarter
10,HTML,markup,FR,2026,1
5,Python,programming,FR,2026,1
3,HTML,markup,US,2025,4
2,Python,programming,US,2025,4
"""


def test_parse_pypl_all_section():
    script = _load_script()
    rows = script.parse_pypl_table(_PYPL_HTML)
    # Only the "All" section is kept; the <image> cell parses as empty.
    assert rows == [(1, "Python", 50.44), (2, "Java", 12.8), (3, "C/C++", 8.57)]


def test_parse_pypl_missing_section():
    script = _load_script()
    assert script.parse_pypl_table("<html>no table here</html>") == []


def test_aggregate_github_latest_quarter():
    script = _load_script()
    rows = script.aggregate_github_languages(_GITHUB_CSV)
    # Only quarter 2026-1 counts: HTML 10, Python 5.
    assert rows == [("HTML", 10), ("Python", 5)]


def test_aggregate_github_empty():
    script = _load_script()
    assert script.aggregate_github_languages("") == []
