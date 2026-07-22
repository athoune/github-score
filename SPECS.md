# GitHub Project Health Scorer — Specification

## 1. Overview

A Python library and CLI that evaluates the maturity, maintenance state, community health, and long-term sustainability of a GitHub project from its repository URL.

The primary output is a **dashboard in the terminal** (TUI). Structured outputs (JSON, Markdown) are planned for a later phase to enable programmatic comparison and LLM-assisted decision making.

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
│   └── tui             # dashboard renderer
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

| Ecosystem | Registry | Detection inputs |
|-----------|----------|------------------|
| Python    | PyPI     | `pyproject.toml`, `setup.py`, `setup.cfg` |
| JavaScript/Node | npm | `package.json` |
| Rust      | crates.io | `Cargo.toml` |
| Go        | pkg.go.dev | `go.mod` |
| Java      | Maven Central | `pom.xml`, `build.gradle*` |
| Ruby      | RubyGems | `*.gemspec` |
| Docker    | Docker Hub | `Dockerfile`, GitHub Container Registry links |
| Containers | GitHub Packages | `ghcr.io` references |

For each detected registry, the tool reports:
- Whether the package exists.
- Latest published version.
- Date of latest publish.
- Approximate download/pull count when available through public APIs.

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

### 7.6 Sustainability and backing

Composite signal based on:

- Presence and content of `FUNDING.yml`.
- Funding platforms detected in README/FUNDING (`github/sponsors`, `opencollective`, `tidelift`, `patreon`, `ko-fi`, `liberapay`, etc.).
- Corporate affiliation of major contributors inferred from email domains and GitHub profiles.
- Membership in a recognized foundation or organization (Apache, CNCF, Linux Foundation, etc.) via topic, owner, or README.
- Mentions in README/GOVERNANCE of corporate backing, maintainers, governance model.
- Optional LLM pass: read README, GOVERNANCE, SECURITY to extract sustainability hints.

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

The LLM is given short text excerpts and asked to return structured JSON:

- Extract mentioned sponsors, backers, or supporting companies.
- Identify governance model (BDFL, core team, foundation, corporate-owned).
- Note any roadmap, security policy, or commercial support mention.
- Flag any concerning language about maintenance status.

The LLM must never produce the final health verdict. Its outputs are signals fed into the dashboard.

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
- `--format tui|json|markdown`: default is `tui`. JSON and Markdown are planned.

### 9.3 Output

The default TUI dashboard displays one panel per indicator family. Each panel shows:

- Raw values.
- A short interpretation (e.g., “Latest release 3 months ago”, “Bus factor: 2”).
- A status glyph: healthy, warning, or critical, based on configurable thresholds.

Example skeleton:

```
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
- No global score; interpretive status glyphs only.

### Phase 2 — Structured outputs

- `json` and `markdown` export formats.
- Batch analysis of multiple repositories.
- Comparison view.

### Phase 3 — Scoring model

- Collect examples and tune thresholds.
- Introduce an optional weighted global score.
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
