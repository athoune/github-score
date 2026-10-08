"""Context guards for facts extracted from prose.

A keyword match in a README is evidence of a *mention*, not of a
*relationship*. Before a mention is treated as a claim, the surrounding
text must be checked:

- negation: "not affiliated with, sponsored by or endorsed by Adobe Inc."
  mentions Adobe but claims the opposite;
- disclaimer / competitor context: "trademarks of", "competitor",
  "alternative to", "clean room";
- license context: an "Apache" token inside "Apache-2.0" or "Apache
  License" is a license, not a foundation membership.

The guards are deliberately small and model-agnostic: they backstop the
regex detectors in the sustainability analyzer.
"""

from __future__ import annotations

import re

_NEGATION_WINDOW = 80
_DISCLAIMER_WINDOW_BEFORE = 120
_DISCLAIMER_WINDOW_AFTER = 60
_LICENSE_WINDOW = 60

_NEGATION_RE = re.compile(
    r"\b(?:not|never|neither|without|unaffiliated|aucun)\b"
    r"|\bno longer\b|\bindependent\s+(?:of|from)\b|\bpas\s+de\b|\bn'est\s+pas\b",
    re.IGNORECASE,
)

_DISCLAIMER_CUES = (
    "trademark",
    "registered trademark",
    "not affiliated",
    "not sponsored",
    "not endorsed",
    "not associated",
    "no affiliation",
    "unaffiliated",
    "independent of",
    "independent from",
    "competitor",
    "competes with",
    "alternative to",
    "clean room",
    "clean-room",
    "n'est pas affilié",
)

_LICENSE_CUES = (
    "license",
    "licence",
    "spdx",
    "dual-licensed",
    "licensed under",
    "apache-2.0",
    "mpl-2.0",
    "epl-",
)

# Versioned SPDX-like tokens ("Apache-2.0", "GPL-3.0") also mark a license.
_SPDX_VERSION_RE = re.compile(r"-(?:[0-9]+)\.(?:[0-9]+)")


def _lower_window(text: str, start: int, end: int, before: int, after: int) -> str:
    """Lowercased slice of ``text`` around ``[start:end]``."""
    return text[max(0, start - before) : min(len(text), end + after)].lower()


def is_negated_before(text: str, index: int, window: int = _NEGATION_WINDOW) -> bool:
    """True when a negation cue precedes ``index`` within ``window`` chars."""
    return bool(_NEGATION_RE.search(text[max(0, index - window) : index]))


def is_disclaimer_context(text: str, start: int, end: int) -> bool:
    """True when the match sits in a disclaimer / competitor sentence."""
    lowered = _lower_window(
        text, start, end, _DISCLAIMER_WINDOW_BEFORE, _DISCLAIMER_WINDOW_AFTER
    )
    return any(cue in lowered for cue in _DISCLAIMER_CUES)


def is_license_context(text: str, start: int, end: int) -> bool:
    """True when the match sits in a license mention, not a relationship."""
    lowered = _lower_window(text, start, end, _LICENSE_WINDOW, _LICENSE_WINDOW)
    return any(cue in lowered for cue in _LICENSE_CUES) or bool(
        _SPDX_VERSION_RE.search(lowered)
    )
