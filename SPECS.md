# GitHub Project Health Scorer — Specification

## 1. Overview

A Python library and CLI that evaluates the maturity, maintenance state, community health, and long-term sustainability of a GitHub project from its repository URL.

The primary output is a **dashboard in the terminal** (TUI). Structured outputs are also available: **JSON** for programmatic comparison and LLM-assisted decision making, and **Markdown** for human-readable reports in pull requests, wikis, and documentation.

The tool favors **quantitative signals** extracted through code. Optional LLM inference is used only for qualitative signals that cannot be reliably derived from APIs or local files.

## 2. Goals

- Provide a quick health overview of any public GitHub repository.
- Support both remote-only analysis and optional local-clone deep analysis.
- Minimize human judgment by deriving as much as possible from code.
- Make LLM usage opt-in, with a local-first default (Ollama).
- Cache all remote calls to avoid repeated network traffic and API rate-limit issues.

## 3. Non-goals

- A single aggregate score in the first version. Scoring will remain a dashboard of sub-indicators until enough real-world examples inform a robust global model.
- Static security auditing or license compliance review beyond classification.
- Project-specific quality metrics such as test coverage, code complexity, or vulnerability scanning.

## 4. User stories

- As a developer, I run `gh-score` inside a cloned repository with no arguments and see a health dashboard.
- As an evaluator, I run `gh-score <repo-url>` to assess a project without cloning it.
- As an architect, I export a JSON/Markdown report to compare several candidate libraries.
- As a privacy-conscious user, I run the tool fully offline: local cache, no LLM, no token.

## 5. High-level architecture

```
gh-score
├── core
│   ├── fetchers        # GitHub API, package registries, local git
│   ├── models          # Repository, Contributor, Release, Indicator
│   ├── analyzers       # one per indicator family
│   └── cache           # persistent HTTP + analysis cache
├── cli
│   ├── commands        # main, report, config
│   └── renderers       # TUI dashboard, JSON, Markdown
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

Detect whether the project is published on official registries based on repository contents and naming:

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
  - License classifier.
  - `home_page`, `project_urls` (docs, changelog, source).
  - `info.author`, `info.maintainer`.
- **Download stats**: Available via [pypistats](https://pypistats.org/) API (`https://pypistats.org/api/packages/{package}/recent`) or BigQuery. Reports downloads per day/month/year.
- **Scope support**: No namespace/scope mechanism. Package names are global and case-insensitive.

#### 6.3.2 Node.js — npm

- **API**: Registry at `https://registry.npmjs.org/{package}` (full metadata) or `https://registry.npmjs.org/{package}/latest` (latest version only).
- **Available data**:
  - All versions with timestamps.
  - `maintainers` list.
  - `repository.url`, `homepage`, `bugs.url`.
  - Deprecated flag per version.
  - `dist-tags` (latest, beta, next, etc.).
- **Download stats**: Dedicated API at `https://api.npmjs.org/downloads/point/{period}/{package}` where period is `last-day`, `last-week`, or `last-month`. Also supports range: `https://api.npmjs.org/downloads/range/{start}:{end}/{package}`.
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
- **Download stats**: Included in the main API response. No separate endpoint needed.
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
- **Download stats**: Included in the main response (`downloads`, `recent_downloads`). Per-version stats available in the `versions` array.
- **Scope support**: No namespace mechanism. Crate names are global, lowercase, using hyphens or underscores (which are equivalent).

#### 6.3.6 Common registry metrics

For each detected registry, the tool reports:
- Whether the package exists on the registry.
- Latest published version and publication date.
- Download/download count when available through public APIs.
- Deprecated/archived status if the registry exposes it.
- License declared on the registry (compared against GitHub-detected license).

#### 6.3.7 Package name resolution from repository

To link a GitHub repository to its registry package(s), the tool attempts:

1. **Manifest detection**: Parse `pyproject.toml` (`[project].name`), `package.json` (`name`), `Cargo.toml` (`[package].name`), `go.mod` (module path), `*.gemspec` (`spec.name`).
2. **Naming heuristics**: When no manifest is available locally, infer the package name from the repository name (lowercased, dashes normalized). This is unreliable and should be flagged as a heuristic.
3. **Cross-reference**: Query the registry API to confirm existence. Report `exists: false` if not found.

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

### 7.6 Sustainability and backing

Composite signal based on:

- Presence and content of `FUNDING.yml`.
- Funding platforms detected in README/FUNDING (`github/sponsors`, `opencollective`, `tidelift`, `patreon`, `ko-fi`, `liberapay`, etc.).
- Corporate affiliation of major contributors inferred from email domains and GitHub profiles.
- Membership in a recognized foundation or organization (Apache, CNCF, Linux Foundation, etc.) via topic, owner, or README.
- Mentions in README/GOVERNANCE of corporate backing, maintainers, governance model.
- Optional LLM pass: read README, GOVERNANCE, SECURITY to extract sustainability hints.

### 7.7 Website availability

Only computed when the repository declares a homepage.

- HTTP status of the homepage (redirects followed).
- Failure classification: `dns` / `timeout` / `http` / `redirect` / `other`.
- Bot-protection flag (`captcha`): the page answers but is behind an
  "I'm not a robot" check (reCAPTCHA, hCaptcha, Cloudflare, generic).
- Status mapping: reachable 2xx → healthy; captcha or timeout → warning;
  DNS failure, HTTP error or redirect loop → critical; no homepage → unknown.

### 7.8 Recommendation

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
| 2 | Homepage down: DNS failure, HTTP error (4xx/5xx) or redirect loop (only when a homepage is declared) | red | Project homepage is down |
| 3 | Homepage degraded: timeout or bot-protection check ("I'm not a robot") | orange | Homepage unreachable or bot-protected |
| 4 | Young, tiny, ≤ 3 authors (article demo) | orange | Ephemeral project |
| 4b | Same, but organization-owned → skipped (org does not create repos for articles) | — | falls through to next branch |
| 5 | Abandoned + widely used | orange | Large project, but now abandoned |
| 6 | Abandoned | red | No commit for N months |
| 7 | Active + ≥ 80% bots | orange | Maintained only by bots |
| 8 | Active + no stable release | orange | Active but not stabilized |
| 9 | Active + declining activity | orange | Well-maintained but in decline |
| 10 | Active + large community, **or LLM: roadmap AND commercial support** | green | Active project with a large community |
| 11 | Active | green | Active project |
| 12 | Maintenance + no release for 6+ months | orange | No new features for N months |
| 13 | Maintenance mode | orange | Project in maintenance mode |
| 13b | LLM: text declares abandonment AND maintenance state unknown | red | Project texts announce its discontinuation |
| 13c | Same, but widely used | orange | Large project, but now abandoned |
| 14 | Unknown + widely used | orange | Widely used despite low maintenance |
| 14b | Unknown + LLM: text active AND (roadmap or commercial support) | green | Active project |
| 15 | Unknown | orange | Insufficient data |

**Modifier — exotic main language:** when the verdict would be green, an
uncommon main language (outside the PYPL top-20 and the GitHub Innovation
Graph top-20) downgrades it to orange ("Main language is uncommon"). It
never upgrades a verdict and never fires on red/orange outcomes.

All thresholds are heuristics, defined as constants in
`src/gh_score/core/analyzers/recommendation.py` and documented in
[`RULES.md`](RULES.md).

## 8. LLM integration

The LLM module is optional and disabled by default.

### 8.1 Providers

- Default: **Ollama** (local, offline-capable).
- Any provider with an OpenAI-compatible chat completions API (OpenAI, Azure OpenAI, Gemini, etc.).

### 8.2 Configuration

```toml
[llm]
provider = "ollama"          # or "openai", "openai-compatible"
model = "llama3.2"
base_url = "http://localhost:11434/v1"
api_key = ""                 # optional, for remote providers
enabled = false              # default
```

### 8.3 LLM responsibilities

The LLM is given short text excerpts (README, GOVERNANCE, SECURITY) and
asked to return structured JSON limited to signals with **no deterministic
implementation**:

- Roadmap / future plans.
- Security policy content (file presence is detected deterministically).
- Commercial support offering.
- Self-declared maintenance state (`active` / `maintenance` / `abandoned` /
  `unknown`).

Sponsors/backers and the governance model are deliberately **not** asked of
the LLM: they already have deterministic regex implementations
(`sustainability.py`).

The LLM must never produce the final health verdict. Its outputs are
signals fed into the decision tree (gated on `llm.enabled`) and the
report, as a dedicated `QualitativeIndicator` (5th indicator family).
Commit data always wins over prose: the text-declared abandonment branch
only fires when the maintenance state is unknown.

### 8.4 Refined recommendation (phase 2)

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

## 9. CLI design

### 9.1 Commands

```
gh-score [URL] [OPTIONS]
gh-score report [URL] [OPTIONS]
gh-score config
```

### 9.2 Modes

- No arguments: inspect the current directory if it is a git clone with a GitHub remote.
- `URL`: analyze the repository remotely.
- `--local PATH`: analyze an existing local clone without using the GitHub API for git data.
- `--remote`: force remote API analysis even when inside a clone.
- `--no-llm`: disable LLM analysis even if configured.
- `--format tui|json|markdown`: default is `tui`. All three formats are supported.

### 9.3 Output

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

## 10. Library API

The library is designed for programmatic use.

```python
from gh_score import analyze_repo

result = analyze_repo("https://github.com/owner/repo")
print(result.release_health.latest_version)
print(result.contributors.bus_factor)
```

Core abstractions:

- `RepoUrl`: parses and validates GitHub URLs.
- `Repository`: aggregate model containing all indicator families.
- `Fetcher` protocol: implemented for GitHub API, local git, and package registries.
- `Analyzer` protocol: each indicator family is an analyzer operating on `Repository`.
- `Cache`: persistent key-value store keyed by URL + fetcher version.

## 11. Caching

All network calls are cached locally.

- Cache key: normalized URL + endpoint + API version or ETag when available.
- TTL: configurable per source. Default 24 hours for GitHub API, 7 days for registry metadata.
- Cache location: platform-appropriate user data directory (`~/.cache/gh-score` on Linux, equivalent elsewhere).
- The cache respects `Cache-Control` headers when provided.
- A `--refresh` flag forces cache invalidation.

## 12. Configuration

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

### 12.1 Internationalization

All user-facing strings are localized: recommendation verdicts and
reasoning, indicator interpretations, TUI dashboard labels, Markdown
report headings and CLI console messages. The language is derived from
the environment using standard locale precedence:

1. `LC_ALL`
2. `LC_MESSAGES`
3. `LANG`

The locale string is reduced to its language code (`fr_FR.UTF-8` →
`fr`). Supported languages: **French** (`fr`) and **English** (`en`).
Unset or unknown locales fall back to English.

The lightweight translation layer (`gh_score.i18n`) is dependency-free:
messages live in a plain catalog dict keyed by stable identifiers, with
one entry per language. Key groups: `rec_*` (recommendation), `int_*`
(analyzer interpretations), `state_*`/`status_*` (enum display labels),
`tui_*`, `md_*` and `cli_*`. The catalog and the decision tree are
documented in [`RULES.md`](RULES.md). Adding a language means adding a
catalog dict plus a test.

Technical strings (exception messages, click help, enum values in JSON)
intentionally stay in English: they are machine-oriented and
language-neutral.

## 13. Security and privacy

- The tool reads only public repository data and local files.
- The GitHub token is never logged.
- LLM prompts do not include proprietary user data unless explicitly passed.
- Local clones are never modified; the tool opens them read-only.

## 14. Roadmap

### Phase 1 — Dashboard foundation

- GitHub API fetchers with caching.
- Local git fallback when inside a clone.
- TUI dashboard for all indicator families.
- Traffic-light recommendation aggregating all indicators (green/orange/red).
- JSON and Markdown export formats.
- No global score; interpretive status glyphs only.

### Phase 2 — Batch and comparison

- Batch analysis of multiple repositories.
- Comparison view.

### Phase 3 — Scoring model

- Collect examples and tune recommendation thresholds.
- Introduce an optional weighted global score, building on the recommendation model.
- Per-family drill-down explanations.

### Phase 4 — Ecosystem depth

- Deeper package registry integration (downloads, deprecation flags).
- Dependency freshness analysis from manifest files.
- Changelog/release notes quality heuristics.

## 15. Open questions

- Should the tool support GitHub Enterprise Server URLs? If so, how is the API base URL configured?
- Should package registry detection attempt to resolve scoped packages (`@org/name`) automatically?
- What is the default behavior when a repository has no releases? Does that lower the maintenance state or the release health status?
- Should contributor affiliation inference respect privacy by avoiding email-domain heuristics unless the user opts in?
