"""Fork classification.

A forked repository is not necessarily an independent project. Two cases:

- **soft fork**: the default branch tracks the parent (at most a handful of
  own commits). The fork is a vehicle for pull requests — development
  happens upstream, so the fork should not be judged on its own.
- **hard fork**: the default branch deliberately diverged from the parent
  (many own commits). The fork IS the project and is analyzed normally,
  with the fork relationship surfaced as an informational fact.

The classification is based on the GitHub compare API result: ``ahead`` is
the number of commits on the fork's default branch that the parent does not
have — i.e. the fork's own development. A stale-but-synced fork (0 ahead,
many behind) is still a soft fork.
"""

from __future__ import annotations

# A fork with at most this many own commits on its default branch is
# considered a PR vehicle (soft fork), not a deliberately diverged project.
_SOFT_FORK_MAX_AHEAD = 10


def classify_fork(ahead: int | None) -> bool | None:
    """Classify a fork as soft (PR vehicle) or hard (deliberately diverged).

    Args:
        ahead: commits ahead of the parent's default branch, or None when
            the divergence could not be measured.

    Returns:
        True for a soft fork, False for a hard fork, None when unknown.
    """
    if ahead is None:
        return None
    return ahead <= _SOFT_FORK_MAX_AHEAD
