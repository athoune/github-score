#!/usr/bin/env python3
"""Auto-clean completed items from TODO.md.

Convention: an item is auto-cleanable when it is marked done AND carries a
completion date on its first line::

    - [x] (done 2026-08-07) Item title — description

Open items (``[ ]``) and done items without a completion date are managed
by hand and are never touched by this script.

Usage::

    python scripts/todo_clean.py            # show what would be removed
    python scripts/todo_clean.py --apply    # actually remove them
    python scripts/todo_clean.py --days 30  # only items completed > 30 days ago
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

DEFAULT_FILE = "TODO.md"

# First line of a checklist item: "- [ ] ..." or "- [x] ..."
_ITEM_RE = re.compile(r"^- \[(?P<check>[ x])\] ")
# Completion date marker, anywhere on the item's first line.
_DONE_DATE_RE = re.compile(r"\(done (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\)")


def _parse_done_date(line: str) -> date | None:
    """Return the completion date of a done item, or None."""
    m = _DONE_DATE_RE.search(line)
    if not m:
        return None
    try:
        return date(
            int(m.group("year")), int(m.group("month")), int(m.group("day"))
        )
    except ValueError:
        return None


def _collect_items(lines: list[str]) -> list[list]:
    """Group lines into items: [start_index, is_done, done_date, lines].

    An item spans its first line plus the following non-empty, indented
    lines; a blank line or a column-0 line (section header) ends it.
    """
    items: list[list] = []
    current: list | None = None
    for i, line in enumerate(lines):
        m = _ITEM_RE.match(line)
        if m:
            if current is not None:
                items.append(current)
            current = [i, m.group("check") == "x", _parse_done_date(line), [line]]
        elif current is not None and line[:1] in (" ", "\t") and line.strip():
            current[3].append(line)
        elif current is not None:
            items.append(current)
            current = None
    if current is not None:
        items.append(current)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove completed (dated) items from TODO.md."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually remove the stale items (default: preview only)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="only remove items completed more than N days ago (default: 0)",
    )
    parser.add_argument("--file", default=DEFAULT_FILE, help="path to TODO.md")
    args = parser.parse_args(argv)

    path = Path(args.file)
    lines = path.read_text(encoding="utf-8").splitlines()

    today = date.today()
    to_remove: set[int] = set()
    for start, is_done, done_date, item_lines in _collect_items(lines):
        if not is_done or done_date is None:
            continue  # open items and undated done items are handled by hand
        if (today - done_date).days < args.days:
            continue
        to_remove.update(range(start, start + len(item_lines)))

    if not to_remove:
        print("Nothing to clean.")
        return 0

    for i in sorted(to_remove):
        print(f"  {lines[i]}")

    if args.apply:
        kept = [line for i, line in enumerate(lines) if i not in to_remove]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        print(f"\nRemoved {len(to_remove)} line(s) from {path}.")
    else:
        print(f"\n{len(to_remove)} line(s) would be removed (use --apply).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
