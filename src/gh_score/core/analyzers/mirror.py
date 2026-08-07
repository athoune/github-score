"""Mirror-only repository detection.

Flags repositories that are pure mirrors: all commits are imported from
an upstream, no original development happens there. The canonical signal
is the GitHub ``mirror_url`` field, but it is only populated by GitHub's
native mirroring feature; manually-pushed mirrors (the common case) are
detected by a text heuristic on the description and README.
"""

from __future__ import annotations

import re

# A "mirror" mention paired with one of these context words points to a
# mirror setup, not to a project that merely talks about mirrors.
_MIRROR_CONTEXT = (
    "read-only",
    "read only",
    "upstream",
    "original",
    "automatically updated",
    "source repository",
    "no development",
    "tracking",
    "sync",
)

# Phrases that indicate a mirror without the word "mirror" itself
# (e.g. gnu-mirror-unofficial: "GNU C Compiler - Official repo link below").
_STANDALONE_PHRASES = (
    "official repo link",
    "mirrored from",
    "mirror of the official",
)

_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def _extract_upstream(text: str) -> str | None:
    """First non-GitHub URL in the text: a GitHub mirror's upstream is
    usually hosted elsewhere."""
    for url in _URL_RE.findall(text):
        url = url.rstrip(".,;")
        if "github.com" not in url:
            return url
    return None


def detect_mirror(
    mirror_url: str | None,
    description: str | None,
    readme: str | None,
) -> tuple[bool, str | None]:
    """Return ``(is_mirror, upstream_url)``.

    Args:
        mirror_url: GitHub API ``mirror_url`` field (None for non-mirrors).
        description: Repository description (may be None).
        readme: README content (may be None).
    """
    if mirror_url:
        return True, mirror_url

    text = " ".join(filter(None, [description or "", (readme or "")[:3000]]))
    lower = text.lower()
    is_mirror = (
        "mirror" in lower and any(ctx in lower for ctx in _MIRROR_CONTEXT)
    ) or any(phrase in lower for phrase in _STANDALONE_PHRASES)

    if not is_mirror:
        return False, None
    return True, _extract_upstream(text)
