"""Tests for the TODO.md auto-cleanup script.

The script is a standalone dev tool (not part of the package), so it is
loaded via importlib and exercised on temp files.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "todo_clean.py"

_FIXTURE = """\
# TODO

## Section

- [x] (done 2026-08-01) Old done item
      with a continuation line
- [ ] Open item — never touched
- [x] Done item without date — never touched
- [x] (done 2099-01-01) Future dated item — never removed

## Another section

- [x] (done 2026-08-01) Second old item
"""


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("todo_clean", _SCRIPT)
    assert spec and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def todo_file(tmp_path):
    path = tmp_path / "TODO.md"
    path.write_text(_FIXTURE, encoding="utf-8")
    return path


def _run(script, todo_file, *args):
    return script.main(["--file", str(todo_file), *args])


class TestTodoClean:
    def test_dry_run_does_not_modify(self, script, todo_file):
        assert _run(script, todo_file) == 0
        assert todo_file.read_text(encoding="utf-8") == _FIXTURE

    def test_apply_removes_only_dated_done_items(self, script, todo_file):
        _run(script, todo_file, "--apply")
        content = todo_file.read_text(encoding="utf-8")
        assert "Old done item" not in content
        assert "Second old item" not in content
        # Open items and undated done items survive.
        assert "- [ ] Open item — never touched" in content
        assert "- [x] Done item without date — never touched" in content
        # Future-dated items are never removed.
        assert "Future dated item" in content

    def test_days_filter(self, script, todo_file):
        # Both dated items are from 2026-08-01; with --days 30 and a
        # fixture clock far in the future they qualify, with --days 999
        # they do not.
        assert _run(script, todo_file, "--days", "999") == 0
        _run(script, todo_file, "--days", "999", "--apply")
        assert "Old done item" in todo_file.read_text(encoding="utf-8")

    def test_clean_file_reports_nothing(self, script, tmp_path):
        empty = tmp_path / "TODO.md"
        empty.write_text("# TODO\n\n- [ ] open\n", encoding="utf-8")
        assert _run(script, empty) == 0
        assert empty.read_text(encoding="utf-8") == "# TODO\n\n- [ ] open\n"
