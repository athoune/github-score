# TODO

Remaining actions after the test-hardening session
(suite at 81% coverage, 247 tests). Last updated: 2026-08-05.

## Tests / coverage

- [ ] `cli/main.py` (59%) — direct tests for the Markdown renderers
      (`_md_*`), `_render_json` and the CLI commands (`analyze`, `report`,
      `config`).
- [ ] `fetchers/github.py` (66%) — cover `_get` (rate-limit warning,
      cache write), `fetch_community_files` (base64 FUNDING.yml),
      `fetch_readme`, `fetch_all` (asyncio.gather).
- [ ] `analyzers/sustainability.py` (71%) — detection helpers tested
      directly: `_detect_foundation`, `_detect_corporate_backing` (regex),
      `_detect_governance_model`.
- [ ] `analyzers/languages.py` (76%) — `_infer_ecosystem` branches.
- [ ] `llm/provider.py` (19%) — deferred by design: test once the LLM
      feature is actually used.

## Code smells (spotted while writing tests)

- [ ] `local_git.py`: the `_extract_package_name` result is called but
      discarded (line ~200); the ecosystem/package is never stored on the
      `Repository` for registry use.
- [ ] `_parse_maven_response` uses naive `datetime.fromtimestamp` (local
      timezone) — inconsistent with the other parsers, which use UTC.
- [ ] `_get_all_pages` mutates its `params` dict in place (aliasing).

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
- [ ] Tune the recommendation thresholds (`RULES.md`) against real
      projects.
- [ ] Decide the fate of `toto.json` (untracked test artifact).
- [ ] Set up CI (pytest + ruff + coverage).

## Workflow

- Never `git push` — pushing is the maintainer's responsibility.
- Commit author: `OpenCode {model} <opencode@garambrogne.net>`.
- One task per commit.
