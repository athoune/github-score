#!/usr/bin/env python3
"""Release a new gh-score version.

Bumps ``pyproject.toml``, syncs ``uv.lock``, builds the distributions, and
creates the release commit and tag. Pushing stays with the maintainer:

    python scripts/release.py 0.10.5    # commit "Release 0.10.5", tag v0.10.5
    python scripts/release.py v0.10.5   # "v" prefix accepted and normalized

The working tree must be clean: a release only changes the version, it
must not carry unrelated edits.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_PYPROJECT_VERSION_RE = re.compile(r'(?m)^(version = ")[^"]*(")')


def _run(
    *args: str,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command in the repository root.

    Streams the output to the terminal unless ``capture`` is set.
    """
    return subprocess.run(
        args,
        cwd=ROOT,
        check=check,
        capture_output=capture,
        text=capture,
    )


def _current_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = _PYPROJECT_VERSION_RE.search(text)
    if not m:
        raise SystemExit('pyproject.toml: no `version = "…"` line found')
    return m.group(1)


def _set_pyproject_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, count = _PYPROJECT_VERSION_RE.subn(
        rf"\g<1>{version}\g<2>", text, count=1
    )
    if count != 1:
        raise SystemExit("pyproject.toml: could not update the version line")
    PYPROJECT.write_text(new_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "version", help="new version, e.g. 0.10.5 (optional 'v' prefix)"
    )
    args = parser.parse_args(argv)

    version = args.version[1:] if args.version.startswith("v") else args.version
    if not _VERSION_RE.match(version):
        raise SystemExit(f"invalid version {args.version!r}: expected e.g. 0.10.5")

    # 1. The working tree must be clean: a release only changes the version.
    status = _run("git", "status", "--porcelain", capture=True).stdout.strip()
    if status:
        raise SystemExit(
            "working tree is not clean — commit or stash your changes first:\n"
            + status
        )

    # 2. Nothing to do when already at this version.
    if _current_pyproject_version() == version:
        raise SystemExit(f"pyproject.toml is already at version {version}")

    # 3. The tag must not exist yet.
    tag_ok = _run(
        "git", "rev-parse", "-q", "--verify", f"refs/tags/v{version}",
        check=False, capture=True,
    ).returncode
    if tag_ok == 0:
        raise SystemExit(f"tag v{version} already exists")

    # 4. Bump the version, let uv sync the lockfile, and check the release
    #    actually builds. `uv lock` is the explicit lock-sync command;
    #    `uv build` doubles as the build sanity check.
    _set_pyproject_version(version)
    _run("uv", "lock")
    _run("uv", "build")

    # 5. uv.lock must now carry the new root version (the whole point of
    #    running uv: the release commit must not leave a drifting lock).
    lock_text = UV_LOCK.read_text(encoding="utf-8")
    if f'version = "{version}"' not in lock_text:
        raise SystemExit(
            f"uv.lock does not mention version {version} — inspect and commit it "
            "manually"
        )

    # 6. Release commit + lightweight tag, matching the repo convention.
    _run("git", "add", "pyproject.toml", "uv.lock")
    _run("git", "commit", "-m", f"Release {version}")
    _run("git", "tag", f"v{version}")

    branch = _run("git", "branch", "--show-current", capture=True).stdout.strip()
    print(f"Released {version} on {branch or '(detached HEAD)'}.")
    print("Remaining (maintainer):  git push && git push --tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
