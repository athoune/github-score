# TODO

Remaining actions after the test-hardening session
(suite at 87% coverage, 347 tests). Last updated: 2026-08-07.

## Tests / coverage

- [ ] `cli/main.py` (79%) — direct tests for the Markdown renderers
      (`_md_*`), `_render_json` and the CLI commands (`analyze`, `report`,
      `config`).
- [ ] `fetchers/github.py` (68%) — cover `_get` (rate-limit warning,
      cache write), `fetch_community_files` (base64 FUNDING.yml),
      `fetch_readme`, `fetch_all` (asyncio.gather).
- [ ] `fetchers/website.py` (93%) — cover the remaining error branches:
      `_classify_request_error` OTHER fallback and the cache round-trip
      error/typo paths.
- [ ] `analyzers/sustainability.py` (70%) — detection helpers tested
      directly: `_detect_foundation`, `_detect_corporate_backing` (regex),
      `_detect_governance_model`.
- [ ] `analyzers/languages.py` (76%) — `_infer_ecosystem` branches.
- [ ] `__main__.py` (0%) — trivial entry point, currently uncovered.
- [x] (done 2026-08-06) `llm/provider.py` (93%) — no longer deferred: the LLM feature is
      now used (warnings, refined recommendation) and covered by
      `test_provider.py` + `test_llm_functional.py` (landed 2026-08-06).
- [x] (done 2026-08-07) `analyzers/website.py` (100%) — fully covered since the website
      feature landed (2026-08-07).

## Code smells (spotted while writing tests)

- [x] (done 2026-08-07) `local_git.py` dead `_extract_package_name` call — removed the
      discarded call plus the duplicate `_detect_ecosystem` /
      `_extract_package_name` helpers; registry lookup now lives solely in
      `registries.py`.
- [x] (done 2026-08-07) `_parse_maven_response` naive `datetime.fromtimestamp` (local
      timezone) — fixed: parses as aware UTC, consistent with the other
      parsers.
- [x] (done 2026-08-06) `_get_all_pages` mutates its `params` dict in place (aliasing) —
      fixed: it now rebinds to a fresh dict (`{**params, ...}`) per request.

## Planned features

Feature ideas carried over from the maintainer's notes (`TODO.txt`),
described cleanly. Scope and acceptance criteria still to be defined per
item.

- [ ] **Broken repository URLs** — malformed URLs are already rejected by
      `RepoUrl.parse`; extend this to repositories that do not exist
      (GitHub API 404) or that are not repositories, with a clear,
      localized error instead of a raw exception.
- [ ] **Mirror-only repositories** — flag repositories that are pure
      mirrors (all commits imported from an upstream, no original
      development), using GitHub API mirror metadata.
- [ ] **Fork divergence analysis** — when the repository is a fork,
      measure how far behind the upstream it is and classify it:
      *soft fork* (kept alive to contribute pull requests) vs *hard fork*
      (deliberately diverged). Sources: API `parent` / `source` fields,
      plus fork intent mentioned in the description or README.
- [ ] **README language detection** — determine whether the README is
      written in English (documentation accessibility signal).
- [ ] **Main-language popularity** — flag exotic (uncommon) primary
      languages against committed CSV datasets built from
      `innovationgraph.github.com/global-metrics/programming-languages`
      and `pypl.github.io/PYPL.html`; ship a helper script to refresh
      the datasets.
- [ ] **Pending security updates (Dependabot)** — surface open Dependabot
      pull requests that address GitHub security advisories (pending
      security fixes).

## Product / housekeeping

- [x] (done 2026-08-05) Upgrade `gitpython` to `>=3.1.57` — fixes 3 open Dependabot
      alerts (1 high, 2 medium) on the default branch:
      - high   GHSA-3f7w-8rr8-f37f — unguarded git option forwarding
        (arbitrary file overwrite/read), patched in 3.1.57
      - medium GHSA-539m-9xh6-q6rr — incomplete `unsafe_git_archive_options`
        denylist (arbitrary file read), patched in 3.1.57
      - medium GHSA-p538-c434-8v24 — arbitrary file truncation via
        `git rev-list --output`, patched in 3.1.56
      (installed: 3.1.54; constraint is already `>=3.1`)
- [x] (done 2026-08-06) Set up CI — `.github/workflows/ci.yml` (pytest + ruff + coverage)
      and `publish.yml` (PyPI on version tags) landed 2026-08-06.
- [ ] Run prospector in CI — `ci.yml` only runs ruff + pytest today;
      pylint/pyright/pycodestyle via prospector run only locally.
- [x] (done 2026-08-07) Decide the fate of `toto.json` — removed; nothing in the codebase
      writes it anymore (it was never tracked).
- [ ] Tune the recommendation thresholds (`RULES.md`) against real
      projects — includes the new website probe heuristics (timeouts,
      redirect cap, captcha markers) and the homepage-downgrade branches.
- [ ] Prune TODO.md items older than one month (periodic cleanup of
      stale entries).

## Workflow

- Never `git push` — pushing is the maintainer's responsibility.
- Commit author: `OpenCode {model} <opencode@garambrogne.net>`.
- One task per commit.
- When an item is done, tick `[x]` and add its completion date:
  `- [x] (done YYYY-MM-DD) …`. The auto-cleanup
  (`python scripts/todo_clean.py --apply`) removes dated done items only;
  open items and undated done items are managed by hand.
