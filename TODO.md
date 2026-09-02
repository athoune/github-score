# TODO

Remaining actions after the comparison feature
(suite at 89% coverage, 564 tests). Last updated: 2026-08-10.

## Tests / coverage

- [ ] `cli/main.py` (84%) — direct tests for the Markdown renderers
      (`_md_*`), `_render_json` and the CLI commands (`analyze`, `report`,
      `config`).
- [ ] `fetchers/github.py` (80%) — cover `_get` (rate-limit warning,
      cache write), `fetch_community_files` (base64 FUNDING.yml),
      `fetch_all` (asyncio.gather).
- [ ] `fetchers/website.py` (93%) — cover the remaining error branches:
      `_classify_request_error` OTHER fallback and the cache round-trip
      error/typo paths.
- [ ] `analyzers/sustainability.py` (70%) — detection helpers tested
      directly: `_detect_foundation`, `_detect_governance_model` (regex)
      still uncovered; `_detect_corporate_backing` is covered (noun-first
      and keyword-first phrasings, self-mention guard).
- [x] (done 2026-08-07) `analyzers/languages.py` (90%) — `_infer_ecosystem`
      branches covered by the popularity feature's tests.
- [ ] `__main__.py` (0%) — trivial entry point, currently uncovered.
- [x] (done 2026-08-06) `llm/provider.py` (93%) — no longer deferred: the LLM feature is
      now used (warnings, refined recommendation) and covered by
      `test_provider.py` + `test_llm_functional.py` (landed 2026-08-06).
- [x] (done 2026-08-07) `analyzers/website.py` (100%) — fully covered since the website
      feature landed (2026-08-07).

## Code smells (spotted while writing tests)

- [x] (done 2026-08-09) PyPI `info.license` full-text spill — the legacy
      field sometimes carries the whole license text (e.g. the `oikb`
      package); `_parse_pypi_response` now prefers `license_expression`
      (PEP 639), then `License ::` Trove classifiers, then the first line
      of the free text. `_compare_licenses` normalizes a trailing
      "license"/"licence" word ("MIT License" == SPDX "MIT") and the Go
      `module.license` object shape (`{"type", "filePath"}`) is guarded.
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

- [x] (done 2026-08-10) **Broken repository URLs** — malformed URLs are
      already rejected by `RepoUrl.parse`; repositories that do not exist
      (GitHub API 404) now raise a clear localized error, and a transient
      API failure (rate limit, auth, network) degrades the analysis with a
      localized warning instead of a raw exception or a ghost report.
- [x] (done 2026-08-07) **Mirror-only repositories** — flag repositories
      that are pure mirrors (all commits imported from an upstream, no
      original development): GitHub `mirror_url` field or a text heuristic
      on description/README; the verdict points at the upstream.
- [x] (done 2026-08-10) **Fork divergence analysis** — when the repository
      is a fork, measure how far its default branch is from the parent's
      default branch and classify it: *soft fork* (at most
      `_SOFT_FORK_MAX_AHEAD` own commits — a vehicle for pull requests,
      judged on the parent like a mirror) vs *hard fork* (deliberately
      diverged — analyzed normally, with a `fact_fork_hard` reasoning line
      and a badge). Sources: API `fork` / `parent` / `source` fields plus
      the compare API (`parent_owner:parent_branch...fork_branch`).
      Follow-ups: the pure-local mode (no remote URL) never has fork data;
      text-intent heuristics from the description/README were not needed
      for the reported cases and can be added later if false negatives
      show up.
- [x] (done 2026-08-10) **Pull requests opened from a fork** — for any
      fork, look up the PRs it opened against its parent (GitHub search
      `repo:{parent} is:pr author:{fork_owner}`, which covers every branch
      of the fork; merged PRs detected via `pull_request.merged_at`) and
      surface them in the report badge (TUI/Markdown) and in the soft-fork
      verdict reasoning. Caveat: search-by-author misses PRs authored by a
      different account using the fork (rare); search indexing lags newly
      opened PRs.
- [x] (done 2026-08-07) **README language detection** — determine whether
      the README is written in English (dependency-free heuristic on the
      dominant script + English stopword density; informational only,
      never affects the verdict).
- [x] (done 2026-08-07) **Main-language popularity** — flag exotic
      (uncommon) primary languages against the committed PYPL top-20 +
      GitHub Innovation Graph top-20 CSVs (`src/gh_score/data/`), with
      `scripts/refresh_language_datasets.py` to refresh them; exotic
      languages downgrade a green verdict to orange.
- [x] (done 2026-08-07) **Pending security updates (Dependabot)** — surface
      open Dependabot PRs that address security advisories (dependabot-core
      body marker); recent updates → orange, updates pending > 3 days → red.
- [x] (done 2026-08-07) **LLM contradiction guard** — the refined
      recommendation prompt forbids denying the extracted qualitative
      signals, and a deterministic windowed heuristic surfaces a warning
      when the recommendation still claims a present fact is absent.
      Extended (2026-08-13) to **unsupported negatives**: claiming the
      absence of a fact the analysis could not verify (e.g. "no roadmap"
      when the README never mentions one) now warns
      (`warn_llm_unsupported_negative`), and the prompt tells the model
      to phrase such claims as "no X announced in the project texts".
- [ ] **LLM contradiction repair loop (optional, off by default)** — when
      the contradiction guard fires, re-ask the LLM once, quoting the
      contradiction (the claim vs the extracted fact), to revise its
      explanation. Disabled by default to avoid an extra LLM call on every
      run; re-evaluate if the guard's hit rate justifies it.
- [x] (done 2026-08-09) **Multi-repo comparison** — `gh-score URL1 URL2
      [URL3…]` analyzes every repository in parallel and renders a
      comparison (condensed no-scroll TUI, JSON, Markdown) with a
      per-pair comparability assessment that never blocks: project
      classification (library/application), consumer languages with
      binding detection (registries + `bindings/*` dirs, JS ≡ TS),
      subject matching (non-generic topics → description keywords →
      unknown). Optional LLM lifts only `unknown` subject verdicts.
      Follow-ups: the subject thresholds were tuned against real
      projects (2026-08-13): the generic token/topic lists held up, and a
      cross-signal rule was added (a topic of one project shared with a
      description keyword of the other — fixes the FastAPI vs Flask
      false negative). New informational pair `similarity` score (0.0–1.0
      lexical closeness, `None` on unjudgeable subjects), a ranked
      decision table (verdict, then downloads, then bus factor; bus
      factor / downloads / latest-release columns; `ranking` in JSON)
      and a **recommended pick** block (starred #1 + verdict message per
      project) answering "which should I pick?" at a glance.
      Remaining:
      detect bindings that never publish to a registry (ctypes/JNI — see
      SPECS §16).
- [x] (done 2026-08-09) **Registry popularity (dependents + downloads)** —
      number of packages depending on the library (reverse dependencies)
      from the official registries (crates.io `meta.total`, RubyGems array
      length, pkg.go.dev `importedBy.total` — the Go counter was dead code
      and misparsed the response; now wired and fixed) and from libraries.io
      for PyPI/npm/Maven when `LIBRARIES_IO_API_KEY` is set. Downloads
      semantics documented per ecosystem (SPECS §6.3.9); npm monthly
      downloads now count toward "widely used"; `fact_dependents` reasoning
      line; registry detection works in remote mode (`root_files` + manifest
      content via the GitHub contents API) with a language-based fallback;
      the Markdown report gained a Package Registries section.
      Follow-ups: RubyGems dependents may be capped for very popular gems
      (approximate floor); libraries.io needs a free API key (60 req/min);
      the `*.gemspec` language fallback still needs a root listing.

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
