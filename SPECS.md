# GitHub Project Health Scorer — Specification

## 1. Overview

A Python library and CLI that evaluates the maturity, maintenance state, community health, and long-term sustainability of a GitHub project from its repository URL.

The primary output is a **dashboard in the terminal** (TUI). Structured outputs are also available: **JSON** for programmatic comparison and LLM-assisted decision making, and **Markdown** for human-readable reports in pull requests, wikis, and documentation.

Several repositories can be compared in a single run (`gh-score URL1 URL2 …`). The tool checks that the comparison is credible — projects must address the same subject, and libraries must be in the same language — and warns otherwise.

The tool favors **quantitative signals** extracted through code. Optional LLM inference is used only for qualitative signals that cannot be reliably derived from APIs or local files.

## 2. Goals

- Provide a quick health overview of any public GitHub repository.
- Support both remote-only analysis and optional local-clone deep analysis.
- Minimize human judgment by deriving as much as possible from code.
- Make LLM usage opt-in, with a local-first default (Ollama).
- Cache all remote calls to avoid repeated network traffic and API rate-limit issues.
- Compare several repositories side by side, flagging comparisons that are
  not credible (different subjects, or different languages for libraries).

## 3. Non-goals

- A single aggregate score in the first version. Scoring will remain a dashboard of sub-indicators until enough real-world examples inform a robust global model.
- Static security auditing or license compliance review beyond classification.
- Project-specific quality metrics such as test coverage, code complexity, or vulnerability scanning.

## 4. User stories

- As a developer, I run `gh-score` inside a cloned repository with no arguments and see a health dashboard.
- As an evaluator, I run `gh-score <repo-url>` to assess a project without cloning it.
- As an architect, I export a JSON/Markdown report to compare several candidate libraries.
- As an architect, I run `gh-score URL1 URL2` to compare candidate projects side by side, and I am warned when they do not address the same subject or, for libraries, are not written in the same language.
- As a privacy-conscious user, I run the tool fully offline: local cache, no LLM, no token.

## 5. High-level architecture

```
gh-score
├── core
│   ├── fetchers        # GitHub API, package registries, local git
│   ├── models          # Repository, Contributor, Release, Indicator
│   ├── analyzers       # one per indicator family
│   ├── comparison      # multi-repo comparison, comparability assessment
│   └── cache           # persistent HTTP + analysis cache
├── cli
│   ├── commands        # analyze (1 URL) / compare (2+ URLs), report, config
│   └── renderers       # TUI dashboard, comparison TUI, JSON, Markdown
├── llm                 # optional provider abstraction
└── config              # settings, credentials, provider selection
```

The library exposes a function-based API returning typed models. The CLI is a thin wrapper over the library.

## 6. Data sources and collection strategy

### 6.1 Primary source: GitHub REST/GraphQL API

Used whenever a repository URL is provided. The following fields are fetched:

- Repository metadata: name, owner, creation date, default branch, archived/disabled status.
- License: `license.spdx_id`, fallback to local `LICENSE` file parsing.
- Releases: latest release, pre-release flag, published date, list of recent releases for cadence analysis.
- Stars, forks, watchers.
- Issues: open and closed counts, average time to close over the last 12 months.
- Commits: default branch commit history (last N commits or 12 months), authors, dates.
- Contributors: GitHub’s contributor statistics endpoint (commits per contributor).
- Languages: GitHub Linguist breakdown.
- Remote metadata: `FUNDING.yml`, `GOVERNANCE.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`.
- Root directory listing (`GET /contents`) — file and directory names at the repository root, used for library/application classification and binding detection.

Authentication:
- If `GITHUB_TOKEN` is present, use it.
- Otherwise, warn the user and fall back to anonymous requests, displaying the remaining unauthenticated rate limit.

### 6.2 Optional source: local git clone

Used when `gh-score` is run inside a git repository with a GitHub remote, or when the user passes `--local /path/to/clone`.

Local analysis can provide:

- Exact commit history and author data.
- Detection of the upstream remote URL.
- File-level metadata (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.).
- Changelog presence (`CHANGELOG.md`, `NEWS.md`, GitHub releases).
- Documentation coverage (`README.md`, docs folder).

The tool **must not** clone repositories automatically. The user can provide an existing clone, or run the tool from one. A future `--clone` flag may be added later.

### 6.3 Package registries

Detect whether the project is published on official registries and collect
popularity metrics (downloads, dependents). Manifest detection works in
**both modes**: from a local clone (filesystem) or remotely via the GitHub
contents API (root file listing + manifest content, see §6.3.7).

| Ecosystem | Registry | Detection inputs | Base API URL |
|-----------|----------|------------------|--------------|
| Python    | PyPI     | `pyproject.toml`, `setup.py`, `setup.cfg` | `https://pypi.org/pypi/{package}/json` |
| JavaScript/Node | npm | `package.json` | `https://registry.npmjs.org/{package}` |
| Rust      | crates.io | `Cargo.toml` | `https://crates.io/api/v1/crates/{crate}` |
| Go        | pkg.go.dev | `go.mod` | `https://pkg.go.dev/v1beta/package/{path}` |
| Ruby      | RubyGems | `*.gemspec`, `Gemfile` | `https://rubygems.org/api/v1/gems/{gem}.json` |
| Java      | Maven Central | `pom.xml`, `build.gradle*` | `https://search.maven.org/solrsearch/select?q=g:{group}+AND+a:{artifact}` |
| Docker    | Docker Hub | `Dockerfile`, `docker-compose.yml` | `https://hub.docker.com/v2/repositories/{namespace}/{name}` |
| Containers | GitHub Packages | `ghcr.io` references | GitHub API (`GET /user/packages/{type}/{name}`) |

#### 6.3.1 Python — PyPI

- **API**: JSON endpoint at `https://pypi.org/pypi/{package}/json`.
- **Available data**:
  - Latest version and all releases with upload dates.
  - `requires_python` (minimum Python version).
  - License: the legacy `license` field often carries the **full license
    text** instead of an identifier (e.g. the `oikb` package). The tool
    prefers `license_expression` (PEP 639), then the `License ::` Trove
    classifier, then reduces the free text to its first line (the license
    name). Registry/GitHub comparison normalizes a trailing
    "license"/"licence" word, so "MIT License" matches the SPDX id "MIT".
  - `home_page`, `project_urls` (docs, changelog, source).
  - `info.author`, `info.maintainer`.
- **Download stats**: Available via [pypistats](https://pypistats.org/) API (`https://pypistats.org/api/packages/{package}/recent`) or BigQuery. Reports downloads per day/month/year. The tool stores `last_month` in `downloads`.
- **Dependents**: Not exposed by the PyPI API. Fetched from [libraries.io](https://libraries.io/api) (`dependents_count`) when a `LIBRARIES_IO_API_KEY` is configured; otherwise `None` (§6.3.8).
- **Scope support**: No namespace/scope mechanism. Package names are global and case-insensitive.

#### 6.3.2 Node.js — npm

- **API**: Registry at `https://registry.npmjs.org/{package}` (full metadata) or `https://registry.npmjs.org/{package}/latest` (latest version only).
- **Available data**:
  - All versions with timestamps.
  - `maintainers` list.
  - `repository.url`, `homepage`, `bugs.url`.
  - Deprecated flag per version.
  - `dist-tags` (latest, beta, next, etc.).
- **Download stats**: Dedicated API at `https://api.npmjs.org/downloads/point/{period}/{package}` where period is `last-day`, `last-week`, or `last-month`. Also supports range: `https://api.npmjs.org/downloads/range/{start}:{end}/{package}`. The tool stores `last-month` in `recent_downloads` (`downloads` stays unset).
- **Dependents**: Not exposed by the npm registry API (the packument has no dependents field). Fetched from libraries.io (`dependents_count`) when a `LIBRARIES_IO_API_KEY` is configured; otherwise `None` (§6.3.8).
- **Scope support**: Scoped packages use `@scope/name` syntax (e.g., `@angular/core`). The API URL encodes the scope as `@scope%2Fname`.

#### 6.3.3 Ruby — RubyGems

- **API**: JSON endpoint at `https://rubygems.org/api/v1/gems/{gem}.json`.
- **Available data**:
  - `version`, `created_at`, `updated_at`.
  - `downloads` (total) and `version_downloads` (for current version).
  - `license` (SPDX string or array).
  - `source_code_uri`, `changelog_uri`, `documentation_uri`, `homepage_uri`, `bug_tracker_uri`.
  - `authors`, `maintainers`.
  - `dependencies` (runtime and development).
- **Download stats**: Included in the main API response. No separate endpoint needed. The tool stores the gem total in `downloads` and `version_downloads` (current version) in `recent_downloads`.
- **Dependents**: `GET /api/v1/gems/{gem}/reverse_dependencies.json` returns the full array of gems depending on this gem (the `page` parameter is ignored; one response holds the whole list). The dependents count is the length of that array. May be capped by RubyGems for very popular gems (§6.3.8).
- **Scope support**: No namespace mechanism. Gem names are global.

#### 6.3.4 Go — pkg.go.dev

- **API**: REST API at `https://pkg.go.dev/v1beta/` (beta, subject to change).
- **Endpoints**:
  - `/v1beta/module/{path}` — module metadata, latest version, licenses.
  - `/v1beta/package/{path}` — package-level docs, imports, imported-by list.
  - `/v1beta/imported-by/{path}` — list of modules importing this package. Key metric for ecosystem adoption.
  - `/v1beta/versions/{path}` — available versions with timestamps.
  - `/v1beta/search?q={query}` — package search.
  - `/v1beta/vulns/{path}` — known vulnerabilities.
- **Download stats**: Not directly available. Proxy.golang.org does not publish download counts. Popularity is inferred via `imported-by` count and GitHub stars.
- **Dependents**: `GET /v1beta/imported-by/{path}` returns `importedBy.total` — the exact number of modules importing this package (§6.3.8).
- **Scope support**: Module paths are URLs by convention (e.g., `github.com/owner/repo`). No formal namespace, but path prefix indicates hosting.

#### 6.3.5 Rust — crates.io

- **API**: REST API at `https://crates.io/api/v1/crates/{crate}`.
- **Available data**:
  - `max_version`, `max_stable_version`, `newest_version`.
  - `downloads` (total), `recent_downloads` (last 90 days).
  - `created_at`, `updated_at`.
  - `license` (SPDX string).
  - `repository`, `homepage`, `documentation`.
  - `categories`, `keywords`, `ategories` (featured status).
  - `versions` array with per-version download counts.
- **Download stats**: Included in the main response (`downloads`, `recent_downloads`). Per-version stats available in the `versions` array. The tool stores the totals as-is.
- **Dependents**: `GET /api/v1/crates/{crate}/reverse_dependencies` returns the exact count in `meta.total` (§6.3.8).
- **Scope support**: No namespace mechanism. Crate names are global, lowercase, using hyphens or underscores (which are equivalent).

#### 6.3.6 Common registry metrics

For each detected registry, the tool reports:
- Whether the package exists on the registry.
- Latest published version and publication date.
- Download/download count when available through public APIs (see §6.3.9 for the per-ecosystem windows).
- Number of dependent packages (reverse dependencies) when the registry or libraries.io exposes it (§6.3.8).
- Deprecated/archived status if the registry exposes it.
- License declared on the registry (compared against GitHub-detected license).

#### 6.3.7 Package name resolution from repository

The package name always comes from a manifest file, never from the
repository name (homonym risk). Manifests are read through a storage
abstraction with two implementations:

- **Local mode** (`LocalManifestStore`): files are listed and read from the
  local clone filesystem.
- **Remote mode** (`RemoteManifestStore`): the root file list comes from
  `community.root_files` (GitHub contents API, already fetched by
  `fetch_community_files`); manifest content is read through
  `GitHubFetcher.fetch_file_content`. When the root listing is unavailable,
  candidate manifests are probed from the primary language (Python →
  `pyproject.toml` → `setup.py` → `setup.cfg`; JS/TS → `package.json`;
  Rust → `Cargo.toml`; Go → `go.mod`; Ruby → `*.gemspec`; Java/Kotlin/Groovy
  → `pom.xml` → `build.gradle(.kts)`).

Resolution steps:

1. **Ecosystem detection**: root manifests matching the known names
   (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `*.gemspec`,
   `pom.xml`, `Dockerfile`, …) select the ecosystems. All present ecosystems
   are kept (multi-ecosystem repositories, e.g. a Rust library with Python
   bindings).
2. **Name extraction**: parse `[project].name` (pyproject.toml),
   `name` (package.json), `[package].name` (Cargo.toml), the `module`
   directive (go.mod), `spec.name` (`*.gemspec`), `groupId:artifactId`
   (pom.xml).
3. **Cross-reference**: query the registry API to confirm existence. Report
   `exists: false` if not found. When no manifest (or no name inside it) is
   found for an ecosystem, that ecosystem is skipped — never guessed.

#### 6.3.8 Popularity: dependents (reverse dependencies)

The number of packages that depend on this library is the primary
popularity signal alongside downloads. Sources per ecosystem:

| Ecosystem | Source | Endpoint | Count |
|-----------|--------|----------|-------|
| crates.io | official | `GET /api/v1/crates/{crate}/reverse_dependencies` | `meta.total` (exact) |
| Go | official | `GET /v1beta/imported-by/{path}` | `importedBy.total` (exact) |
| RubyGems | official | `GET /api/v1/gems/{gem}/reverse_dependencies.json` | `len(array)` (single response) |
| PyPI | libraries.io | `GET /api/Pypi/{name}` | `dependents_count` |
| npm | libraries.io | `GET /api/NPM/{name}` | `dependents_count` |
| Maven | libraries.io | `GET /api/Maven/{group}/{artifact}` | `dependents_count` |
| Docker | — | — | `None` |

[libraries.io](https://libraries.io/api) is a third-party aggregator
(Tidelift/Sonar) requiring a free API key (env `LIBRARIES_IO_API_KEY` or
`[registries] libraries_io_api_key` in `config.toml`, 60 requests/minute).
It only fills the gaps left by official registries, never replaces them.
Without a key, `dependents` stays `None` for PyPI/npm/Maven — no warning is
emitted: downloads remain available for those ecosystems.

#### 6.3.9 Download statistics semantics

`downloads` and `recent_downloads` do not cover the same window across
ecosystems — thresholds must be read with this in mind:

| Ecosystem | `downloads` | `recent_downloads` |
|-----------|-------------|--------------------|
| PyPI | last 30 days (pypistats `last_month`) | — |
| npm | — (unset) | last 30 days (`api.npmjs.org` `last-month`) |
| crates.io | total | last 90 days |
| RubyGems | total | current-version downloads (`version_downloads`) |
| Maven | total (`downloadCount`) | — |
| Go | — (not published by proxy.golang.org) | — |
| Docker | total pulls (`pull_count`) | — |

Both fields are treated as adoption proxies by the recommendation (see the
`_WIDELY_USED_*` thresholds in RULES.md); the differing windows are
documented here to avoid false equivalence.

### 6.4 Website availability

When the repository declares a homepage (`RepositoryMeta.homepage`), the
tool probes it to check that the application's web page is actually
reachable. Repos without a homepage skip this check (the indicator is
`UNKNOWN`).

- **Method**: HTTP GET with `follow_redirects=True` (bounded at 10
  redirects), connect timeout 10 s / read timeout 15 s. Only a limited
  body sample (64 KiB) is downloaded.
- **Failure classification**:
  - DNS resolution failure → `dns` (red flag)
  - Connect/read timeout → `timeout` (degraded)
  - HTTP 4xx/5xx status → `http` (red flag)
  - Redirect loop → `redirect` (red flag)
  - Anything else (TLS, invalid URL, …) → `other` (degraded)
- **Bot protection**: a keyword heuristic on response headers, title and
  the first 64 KiB of HTML detects "I'm not a robot" pages (reCAPTCHA,
  hCaptcha, Cloudflare challenge/turnstile, generic "verify you are
  human" text). Such pages count as degraded (the site is up but cannot
  be read by the tool).
- **Caching**: results (successes and failures alike) are cached with the
  default TTL.

### 6.5 Main-language popularity

Two small datasets are committed under `src/gh_score/data/` and shipped in
the wheel:

- `pypl_languages.csv` — top-20 of the PYPL index (rank, language, share).
- `github_languages.csv` — top-20 languages by number of pushers in the
  most recent quarter of the GitHub Innovation Graph.

Refresh them with `python scripts/refresh_language_datasets.py` (default
top 20). The analyzer flags the primary language as **exotic** when it is
not in either dataset (with a small alias map: PYPL "C/C++" covers
Linguist "C" and "C++", "Visual Basic" covers "VBA", …).

### 6.6 Pending security updates (Dependabot)

Open pull requests authored by a Dependabot bot whose body carries the
dependabot-core security marker ("This update includes a security fix.")
are pending security updates. The marker is required: plain version bumps
also mention "security" inside the dependency changelog. Fetched from
`GET /repos/{owner}/{repo}/pulls?state=open`.

### 6.7 Mirror-only repositories

A repository is flagged as a mirror when no development happens in it:
- the GitHub API `mirror_url` field is set (GitHub's native mirroring
  feature), or
- the description or README says so ("read-only mirror", "mirror of the
  original", "official repo link below", …) — the fallback heuristic for
  manually-pushed mirrors, which never populate `mirror_url`.

### 6.8 Fork divergence

A forked repository is not necessarily an independent project. The tool
captures the GitHub API `fork` flag plus the `parent` / `source`
`full_name` fields and measures how far the fork's default branch is from
its parent's default branch (GitHub compare API with cross-repository
refs: `parent_owner:parent_branch...fork_branch`), yielding `ahead_by`
(the fork's own commits) and `behind_by` (staleness).

Classification (`classify_fork`, threshold `_SOFT_FORK_MAX_AHEAD = 10` in
`analyzers/fork.py`):

- **soft fork** (`ahead_by ≤ 10`): the default branch tracks the parent —
  the fork is a vehicle for pull requests, or a stale clone. Its
  maintenance/contributor signals reflect the parent, so the verdict
  downgrades to orange and points at the parent (like a mirror).
- **hard fork** (`ahead_by > 10`): the default branch deliberately
  diverged — the fork IS the project. The normal verdict stands, with the
  fork relationship surfaced as a `fact_fork_hard` reasoning line and a
  badge in the report.
- **unknown** (divergence could not be measured): the normal verdict
  stands.

**Pull requests opened from the fork**: the tool looks up the PRs a fork
has opened against its parent via the GitHub search API
(`repo:{parent} is:pr author:{fork_owner}`) — the search covers every
branch of the fork, unlike the pulls endpoint's `head` filter which
requires an exact `owner:branch` pair. Merged PRs (`state: closed` with
`pull_request.merged_at`) are reported as "merged". The PRs are listed in
the report badge (TUI and Markdown) and in the soft-fork verdict reasoning.

The divergence and PR lookups only run when a GitHub fetcher is available
(remote analysis, or local analysis enriched from a URL); a pure local
clone without a remote URL never has fork data.

## 7. Indicator families

The dashboard is organized into the following families. Each family exposes several concrete indicators. No global score is computed in the first version.

### 7.1 Release health

- Latest release version and date.
- Age of latest release (days).
- Release cadence: average days between releases over the last 12 months.
- Semver compliance: whether tags/releases follow semantic versioning.
- Stability flag: whether the latest release is a pre-release.

### 7.2 License

- Declared SPDX identifier from GitHub API.
- Fallback license detected from `LICENSE` file text.
- OSI-approved flag.
- Family: permissive, copyleft, public domain, proprietary/other.

### 7.3 Community and contributors

Analysis spans the full project history to detect leadership transitions.

- Total number of commit authors.
- Contribution distribution: top contributors by commit count and lines changed.
- Bus factor: smallest number of contributors accounting for 50% of commits.
- Bot ratio: commits authored by known bots (Dependabot, Renovate, GitHub Actions, etc.).
- Contributor archetypes:
  - **Lead**: dominant contributor over the last 12 months.
  - **Historical lead**: dominant contributor over the full history but no longer active.
  - **Minor contributors**: authors with very few commits, typically drive-by fixes.
- Activity trend over the last 3, 6, 12, and 24 months.

### 7.4 Maintenance state

Derived from commit and issue activity.

- Date of last commit on the default branch.
- Date of last closed issue or merged PR.
- Commit frequency: commits per month over the last 12 months.
- Issue velocity: median time to close issues created in the last 12 months.
- Stale issue ratio: open issues older than 12 months / total open issues.
- Classification:
  - **Active**: commits within the last month and consistent activity.
  - **Maintenance mode**: infrequent commits, issues still closed, no major development.
  - **Abandoned**: no commit for 6+ months, stale issues accumulating.

### 7.5 Languages

- Primary language.
- Full language breakdown from GitHub Linguist.
- Ecosystem inference from manifest files (Python, Node, Rust, Go, etc.).
- Main-language popularity: `is_exotic` flag, best popularity rank and
  source (`pypl` / `github`) from the committed datasets.
- README language: `readme_is_english` flag (dependency-free heuristic on
  the dominant script + English stopword density; informational only, it
  never affects the verdict).

### 7.6 Sustainability and backing

Composite signal based on:

- Presence and content of `FUNDING.yml`.
- Funding platforms detected in README/FUNDING (`github/sponsors`, `opencollective`, `tidelift`, `patreon`, `ko-fi`, `liberapay`, etc.).
- Membership in a recognized foundation or organization (Apache, CNCF, Linux Foundation, etc.) via topic, owner, or README.
- Explicit mentions in README/GOVERNANCE of corporate backing, maintainers, governance model. Corporate backing is detected from two
  phrasings: keyword-first (`backed by <Company>`, `sponsored by <Company>`, …) and noun-first (`<Company> is a/the (founding) sponsor/backer of
  …`). A sentence where the repository names itself as the sponsor of others ("Warp is a sponsor of X") is not backing of that repository and is
  ignored.
- Optional LLM pass: read README, GOVERNANCE, SECURITY to extract sustainability hints.

### 7.7 Website availability

Only computed when the repository declares a homepage.

- HTTP status of the homepage (redirects followed).
- Failure classification: `dns` / `timeout` / `http` / `redirect` / `other`.
- Bot-protection flag (`captcha`): the page answers but is behind an
  "I'm not a robot" check (reCAPTCHA, hCaptcha, Cloudflare, generic).
- Status mapping: reachable 2xx → healthy; captcha or timeout → warning;
  DNS failure, HTTP error or redirect loop → critical; no homepage → unknown.

### 7.8 Security updates

- Number of open Dependabot security PRs (pending updates).
- Age in days of the oldest one.
- Status mapping: none → healthy; pending ≤ 3 days → warning; pending
  > 3 days → critical (a known vulnerability being ignored).

### 7.9 Recommendation

Cross-cutting verdict that aggregates every other indicator family into a
single traffic-light judgment. It answers the question *"should I bet on
this project?"* rather than evaluating one indicator in isolation.

Output:

- **Level**: `green` / `orange` / `red` (traffic light).
- **Message**: a contextual, localized sentence (e.g. "Large project, but
  now abandoned").
- **Confidence**: 0.0 → 1.0, the fraction of core indicators whose status
  is known (data completeness, not project quality).
- **Reasoning**: the triggering signal plus objective facts (stars, author
  count, owner type).

Decision tree (first matching branch wins):

| # | Condition | Level | Message |
|---|-----------|-------|---------|
| 1 | Archived / disabled / registry deprecated | red | Archived / Disabled / Deprecated |
| 2 | Security update pending > 3 days (open Dependabot security PR) | red | Known security vulnerabilities unpatched |
| 3 | Fresh security updates pending (≤ 3 days) | orange | Recent security updates pending |
| 4 | Mirror-only repository (no development happens here) | orange | Repository is a mirror of an upstream project |
| 5 | Homepage down: DNS failure, HTTP error (4xx/5xx) or redirect loop (only when a homepage is declared) | red | Project homepage is down |
| 6 | Homepage degraded: timeout or bot-protection check ("I'm not a robot") | orange | Homepage unreachable or bot-protected |
| 7 | Young, tiny, ≤ 3 authors (article demo) | orange | Ephemeral project |
| 7b | Same, but organization-owned → skipped (org does not create repos for articles) | — | falls through to next branch |
| 8 | Abandoned + widely used | orange | Large project, but now abandoned |
| 9 | Abandoned | red | No commit for N months |
| 10 | Active + ≥ 80% bots | orange | Maintained only by bots |
| 11 | Active + no stable release | orange | Active but not stabilized |
| 12 | Active + declining activity | orange | Well-maintained but in decline |
| 13 | Active + large community, **or LLM: roadmap AND commercial support** | green | Active project with a large community |
| 14 | Active | green | Active project |
| 15 | Maintenance + no release for 6+ months | orange | No new features for N months |
| 16 | Maintenance mode | orange | Project in maintenance mode |
| 16b | LLM: text declares abandonment AND maintenance state unknown | red | Project texts announce its discontinuation |
| 16c | Same, but widely used | orange | Large project, but now abandoned |
| 17 | Unknown + widely used | orange | Widely used despite low maintenance |
| 17b | Unknown + LLM: text active AND (roadmap or commercial support) | green | Active project |
| 18 | Unknown | orange | Insufficient data |

**Modifier — exotic main language:** when the verdict would be green, an
uncommon main language (outside the PYPL top-20 and the GitHub Innovation
Graph top-20) downgrades it to orange ("Main language is uncommon"). It
never upgrades a verdict and never fires on red/orange outcomes.

All thresholds are heuristics, defined as constants in
`src/gh_score/core/analyzers/recommendation.py` and documented in
[`RULES.md`](RULES.md).

## 8. Multi-repository comparison

Comparing several projects is only credible when they address the same
subject and, for libraries, are written in the same language. Passing two
or more URLs analyzes every repository (in parallel) and renders a
comparison with a **comparability assessment**: pairs that are not
credible are flagged, and the user is informed why. The comparison is
never blocked — warnings are informational, the comparison is always
produced.

The comparability rules are deterministic by default. The LLM is optional
and, when enabled, can only refine the cases where the deterministic
rules cannot decide (see §9.3).

### 8.1 Trigger

- `gh-score URL1 URL2 [URL3…]` → comparison mode (2+ URLs).
- `gh-score URL` or `gh-score` alone → single-repository analysis.

### 8.2 Project classification (library / application)

Each project is classified deterministically as `library`, `application`
or `unknown`. The first matching rule wins:

1. Published on a code registry (PyPI, npm, crates.io, RubyGems, Maven,
   Go — Docker excluded) → `library`.
2. `Dockerfile` / `docker-compose*` at the repository root and no
   manifest → `application`.
3. Description keywords: library words (`library`, `framework`, `sdk`,
   `toolkit`, `binding`, `wrapper`, `client`, …) without application
   words → `library`; application words (`application`, `app`, `cli`,
   `tool`, `server`, `daemon`, `bot`, `website`, `service`, …) without
   library words → `application`.
4. A manifest exists (`pyproject.toml`, `package.json`, `Cargo.toml`,
   `go.mod`, `*.gemspec`, `pom.xml`) → `library`.
5. Otherwise → `unknown`.

An `unknown` project is relaxed to `application` for the language rules:
without evidence that a project is a library, its language is not a
comparability constraint. The report notes the unknown kind.

### 8.3 Consumer languages

A library is consumed through its **consumer languages**: the primary
language (Linguist) plus the languages implied by its registry
publications (PyPI → python, npm → javascript, crates.io → rust,
RubyGems → ruby, Maven → java, Go → go). A Rust project published on
PyPI therefore exposes Python bindings: its consumer set is
`{rust, python}` and it can be compared with a Python library.

Normalization: JavaScript and TypeScript count as the same language
(`javascript`); C/C++ aliases follow the language datasets.

Two libraries are **language-compatible** when their consumer-language
sets intersect. Applications (and unknown projects) are exempt from the
language rule.

### 8.4 Subject comparability

Per pair, the subject is assessed deterministically. Language-name and
other generic topics (e.g. "python", "framework", "hacktoberfest") carry
no subject information and are excluded:

1. Both projects have non-generic topics → a shared topic means
   `compatible`. Disjoint non-generic topics are not decisive on their
   own: the descriptions get a second opinion below.
2. Otherwise, both have a description → at least one shared meaningful
   token (stopwords and generic tokens — language names, "tool",
   "library", "project", … — excluded) means `compatible`, otherwise
   `incompatible`.
3. Otherwise → `unknown` (not enough signal to judge; the pair is
   flagged for information).

When the LLM is enabled, it judges subject equivalence from topics,
descriptions and README excerpts. It only lifts a deterministic
`unknown` to `compatible` / `incompatible`; it never overrides a
deterministic verdict (same principle as the maintenance branches:
commit data wins over prose).

### 8.5 Pair verdict

A pair is flagged as a **warning** when:

- the subjects are `incompatible` (or `unknown` — informational);
- both projects are libraries with disjoint consumer languages;
- one project is a library and the other an application (different kinds
  are rarely comparable).

Otherwise the pair is `ok`. Warnings never prevent the comparison.

### 8.6 Output

The comparison reuses the three output formats:

- **TUI**: a condensed view that fits in a terminal window without
  scrolling — a comparability block listing the flagged pairs with their
  reasons, then a table with one line per project (project, stars,
  license, language, maintenance state, last commit, traffic-light
  verdict).
- **JSON**: `{"projects": [...], "pairs": [...], "warnings": [...]}`
  where `projects` are full `AnalysisResult` objects and `pairs` carry
  the comparability verdicts (`subject`, `language_compatible`, kinds,
  reasons).
- **Markdown**: a comparability section, a comparison table, then the
  full per-project report (same content as a single analysis).

## 9. LLM integration

The LLM module is optional and disabled by default.

### 9.1 Providers

- Default: **Ollama** (local, offline-capable).
- Any provider with an OpenAI-compatible chat completions API (OpenAI, Azure OpenAI, Gemini, etc.).

### 9.2 Configuration

```toml
[llm]
provider = "ollama"          # or "openai", "openai-compatible"
model = "llama3.2"
base_url = "http://localhost:11434/v1"
api_key = ""                 # optional, for remote providers
enabled = false              # default
```

### 9.3 LLM responsibilities

The LLM is given short text excerpts (README, GOVERNANCE, SECURITY) and
asked to return structured JSON limited to signals with **no deterministic
implementation**:

- Roadmap / future plans.
- Security policy content (file presence is detected deterministically).
- Commercial support offering.
- Self-declared maintenance state (`active` / `maintenance` / `abandoned` /
  `unknown`).
- Subject comparability (comparison mode only): given topics,
  descriptions and README excerpts of several projects, judge whether
  each pair addresses the same subject. Only lifts a deterministic
  `unknown`; never overrides `compatible` / `incompatible`.

Sponsors/backers and the governance model are deliberately **not** asked of
the LLM: they already have deterministic regex implementations
(`sustainability.py`).

The LLM must never produce the final health verdict. Its outputs are
signals fed into the decision tree (gated on `llm.enabled`) and the
report, as a dedicated `QualitativeIndicator` (5th indicator family).
Commit data always wins over prose: the text-declared abandonment branch
only fires when the maintenance state is unknown.

### 9.4 Refined recommendation (phase 2)

When the LLM is enabled, a second, complementary verdict is produced from
a compact digest of **every indicator family** (metadata, maintenance,
contributors, releases, license, sustainability, qualitative signals,
registries) plus the deterministic verdict:

- **Level**: `green` / `orange` / `red`.
- **Message**: a short verdict sentence.
- **Explanation**: 2-4 sentences weighing the strongest signals and
  trade-offs.
- **Confidence**: the LLM's self-assessed confidence (0.0 → 1.0).

Rules:

- The refined recommendation is informational and **never replaces** the
  deterministic verdict — both are displayed, the report's traffic light
  stays deterministic.
- The LLM may agree with or refine/disagree with the deterministic
  verdict; both are shown side by side.
- Invalid output (unknown level, unparseable JSON, provider failure)
  hides the refined panel without breaking the pipeline.

## 10. CLI design

### 10.1 Commands

```
gh-score [URL] [OPTIONS]
gh-score URL1 URL2 [URL3…] [OPTIONS]   # comparison mode (2+ URLs, see §8)
gh-score report [URL] [OPTIONS]
gh-score config
```

### 10.2 Modes

- No arguments: inspect the current directory if it is a git clone with a GitHub remote.
- `URL`: analyze the repository remotely.
- `URL1 URL2 [URL3…]`: comparison mode — every URL is analyzed (in
  parallel, sharing the cache), then a comparison with a comparability
  assessment is rendered (see §8).
- `--local PATH`: analyze an existing local clone without using the GitHub API for git data.
- `--remote`: force remote API analysis even when inside a clone.
- `--no-llm`: disable LLM analysis even if configured.
- `--format tui|json|markdown`: default is `tui`. All three formats are supported.

### 10.3 Output

Three output formats are available via `--format`:

#### TUI (default)

The default TUI dashboard displays one panel per indicator family. Each panel shows:

- Raw values.
- A short interpretation (e.g., “Latest release 3 months ago”, “Bus factor: 2”).
- A status glyph: healthy, warning, or critical, based on configurable thresholds.

A **traffic-light recommendation panel** (green/orange/red) is displayed
prominently at the top, summarizing the overall verdict with the message,
reasoning and confidence.

Example skeleton:

```
┌─ Recommendation ─────────────────────────────────┐
│ 🟢 Active project with a large community          │
│   • active state, regular development             │
│   • 15,000 stars                                  │
│   • 150 authors                                   │
│   confidence: 100%                                │
└───────────────────────────────────────────────────┘
┌─ Release Health ─────────────────┐ ┌─ License ─────────────┐
│ latest: v2.4.1 (2025-05-10)      │ │ MIT (permissive, OSI) │
│ cadence: 14 days/release         │ └───────────────────────┘
│ status: healthy                  │
└──────────────────────────────────┘
┌─ Contributors ───────────────────┐ ┌─ Maintenance ─────────┐
│ total: 45                        │ │ last commit: 2d ago   │
│ bus factor: 3                    │ │ state: active         │
│ bots: 12%                        │ │ issues closed: 4d     │
│ lead: alice (12m)                │ └───────────────────────┘
└──────────────────────────────────┘
```

#### JSON

Machine-readable output via `--format json`. Emits a single JSON object containing all indicator families as nested dataclasses (serialized with `dataclasses.asdict`). Suitable for:

- Programmatic comparison of multiple repositories.
- Feeding into dashboards, databases, or LLM pipelines.
- CI/CD quality gates.

The JSON output contains the same data as the TUI dashboard: `url`, `meta`, `release_health`, `license`, `contributors`, `maintenance`, `languages`, `sustainability`, `registries`, and `recommendation`.

#### Markdown

Human-readable report via `--format markdown`. Emits a structured Markdown document suitable for:

- Pull request comments and reviews.
- Project wikis and documentation.
- READMEs and comparison tables.

The Markdown output includes section headings per indicator family, key metrics as bullet points, and license/registry status. The **Recommendation** section (traffic-light verdict) is emitted first.

#### Comparison output

With two or more URLs, the renderers switch to comparison mode (§8):

- **TUI**: condensed, fits a terminal window without scrolling — a
  comparability block listing the flagged pairs with their reasons, then
  a table with one line per project (project, stars, license, language,
  maintenance state, last commit, traffic-light verdict).
- **JSON**: `{"projects": [...], "pairs": [...], "warnings": [...]}`
  where `projects` are full `AnalysisResult` objects.
- **Markdown**: comparability section, comparison table, then the full
  per-project report.

## 11. Library API

The library is designed for programmatic use.

```python
from gh_score import analyze_repo, compare_repos

result = analyze_repo("https://github.com/owner/repo")
print(result.release_health.latest_version)
print(result.contributors.bus_factor)

comparison = compare_repos([
    "https://github.com/owner/lib-a",
    "https://github.com/other/lib-b",
])
print(comparison.pairs[0].verdict)  # comparability warning, if any
```

Core abstractions:

- `RepoUrl`: parses and validates GitHub URLs.
- `Repository`: aggregate model containing all indicator families.
- `Fetcher` protocol: implemented for GitHub API, local git, and package registries.
- `Analyzer` protocol: each indicator family is an analyzer operating on `Repository`.
- `Cache`: persistent key-value store keyed by URL + fetcher version.
- `ComparisonResult`: aggregate of `AnalysisResult` objects plus per-pair
  comparability assessments (`PairComparison`).

## 12. Caching

All network calls are cached locally.

- Cache key: normalized URL + endpoint + API version or ETag when available.
- TTL: configurable per source. Default 24 hours for GitHub API, 7 days for registry metadata.
- Cache location: platform-appropriate user data directory (`~/.cache/gh-score` on Linux, equivalent elsewhere).
- The cache respects `Cache-Control` headers when provided.
- A `--refresh` flag forces cache invalidation.

## 13. Configuration

Configuration is read from:

1. Command-line flags.
2. Environment variables (`GITHUB_TOKEN`, `GH_SCORE_*`).
3. User config file: `~/.config/gh-score/config.toml`.

Example config:

```toml
[github]
token = ""

[cache]
dir = ""
ttl_hours = 24

[llm]
enabled = false
provider = "ollama"
model = "llama3.2"
base_url = "http://localhost:11434/v1"
api_key = ""

[dashboard]
colors = true
thresholds = { stale_days = 180, maintenance_commits_per_month = 2 }
```

### 13.1 Internationalization

All user-facing strings are localized: recommendation verdicts and
reasoning, indicator interpretations, comparison messages, TUI dashboard
labels, Markdown report headings and CLI console messages. The language
is derived from the environment using standard locale precedence:

1. `LC_ALL`
2. `LC_MESSAGES`
3. `LANG`

The locale string is reduced to its language code (`fr_FR.UTF-8` →
`fr`). Supported languages: **French** (`fr`) and **English** (`en`).
Unset or unknown locales fall back to English.

The lightweight translation layer (`gh_score.i18n`) is dependency-free:
messages live in a plain catalog dict keyed by stable identifiers, with
one entry per language. Key groups: `rec_*` (recommendation), `int_*`
(analyzer interpretations), `cmp_*` (comparison), `state_*`/`status_*`
(enum display labels), `tui_*`, `md_*` and `cli_*`. The catalog and the
decision tree are documented in [`RULES.md`](RULES.md). Adding a language
means adding a catalog dict plus a test.

Technical strings (exception messages, click help, enum values in JSON)
intentionally stay in English: they are machine-oriented and
language-neutral.

## 14. Security and privacy

- The tool reads only public repository data and local files.
- The GitHub token is never logged.
- LLM prompts do not include proprietary user data unless explicitly passed.
- Local clones are never modified; the tool opens them read-only.

## 15. Roadmap

### Phase 1 — Dashboard foundation

- GitHub API fetchers with caching.
- Local git fallback when inside a clone.
- TUI dashboard for all indicator families.
- Traffic-light recommendation aggregating all indicators (green/orange/red).
- JSON and Markdown export formats.
- No global score; interpretive status glyphs only.

### Phase 2 — Batch and comparison

- Comparison view (done 2026-08-09): `gh-score URL1 URL2 …` analyzes
  several repositories in parallel, assesses pair comparability (subject;
  language for libraries, with binding detection) and warns when the
  comparison is not credible.
- Batch analysis of multiple repositories: the comparison mode already
  analyzes every URL; a standalone batch export without the comparability
  assessment can be added later.

### Phase 3 — Scoring model

- Collect examples and tune recommendation thresholds.
- Introduce an optional weighted global score, building on the recommendation model.
- Per-family drill-down explanations.

### Phase 4 — Ecosystem depth

- Deeper package registry integration (dependents via libraries.io without
  key for more platforms; per-version download history).
- Dependency freshness analysis from manifest files.
- Changelog/release notes quality heuristics.

## 16. Open questions

- Should the tool support GitHub Enterprise Server URLs? If so, how is the API base URL configured?
- What is the default behavior when a repository has no releases? Does that lower the maintenance state or the release health status?
- Should contributor affiliation inference respect privacy by avoiding email-domain heuristics unless the user opts in?
- How far should binding detection go? Registries and root-file manifests cover the common cases (PyPI/npm/crates). Bindings that never publish to a registry (ctypes, JNI, …) are not detected.
