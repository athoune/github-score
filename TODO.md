# TODO

Remaining actions after the test-hardening session
(suite at 86% coverage, 312 tests). Last updated: 2026-08-07.

## Tests / coverage

- [ ] `cli/main.py` (79%) — direct tests for the Markdown renderers
      (`_md_*`), `_render_json` and the CLI commands (`analyze`, `report`,
      `config`).
- [ ] `fetchers/github.py` (68%) — cover `_get` (rate-limit warning,
      cache write), `fetch_community_files` (base64 FUNDING.yml),
      `fetch_readme`, `fetch_all` (asyncio.gather).
- [ ] `analyzers/sustainability.py` (70%) — detection helpers tested
      directly: `_detect_foundation`, `_detect_corporate_backing` (regex),
      `_detect_governance_model`.
- [ ] `analyzers/languages.py` (76%) — `_infer_ecosystem` branches.
- [ ] `__main__.py` (0%) — trivial entry point, currently uncovered.
- [x] `llm/provider.py` (93%) — no longer deferred: the LLM feature is
      now used (warnings, refined recommendation) and covered by
      `test_provider.py` + `test_llm_functional.py` (landed 2026-08-06).

## Code smells (spotted while writing tests)

- [x] `local_git.py` dead `_extract_package_name` call — removed the
      discarded call plus the duplicate `_detect_ecosystem` /
      `_extract_package_name` helpers; registry lookup now lives solely in
      `registries.py`.
- [x] `_parse_maven_response` naive `datetime.fromtimestamp` (local
      timezone) — fixed: parses as aware UTC, consistent with the other
      parsers.
- [x] `_get_all_pages` mutates its `params` dict in place (aliasing) —
      fixed: it now rebinds to a fresh dict (`{**params, ...}`) per request.

## Product / housekeeping

- [x] Upgrade `gitpython` to `>=3.1.57` — fixes 3 open Dependabot
      alerts (1 high, 2 medium) on the default branch:
      - high   GHSA-3f7w-8rr8-f37f — unguarded git option forwarding
        (arbitrary file overwrite/read), patched in 3.1.57
      - medium GHSA-539m-9xh6-q6rr — incomplete `unsafe_git_archive_options`
        denylist (arbitrary file read), patched in 3.1.57
      - medium GHSA-p538-c434-8v24 — arbitrary file truncation via
        `git rev-list --output`, patched in 3.1.56
      (installed: 3.1.54; constraint is already `>=3.1`)
- [x] Set up CI — `.github/workflows/ci.yml` (pytest + ruff + coverage)
      and `publish.yml` (PyPI on version tags) landed 2026-08-06.
- [x] Decide the fate of `toto.json` — removed; nothing in the codebase
      writes it anymore (it was never tracked).
- [ ] Tune the recommendation thresholds (`RULES.md`) against real
      projects.

## Workflow

- Never `git push` — pushing is the maintainer's responsibility.
- Commit author: `OpenCode {model} <opencode@garambrogne.net>`.
- One task per commit.
