# GitHub Score - Implementation Summary

## Overview
A Python library and CLI tool that evaluates the maturity, maintenance state, community health, and long-term sustainability of GitHub projects.

## Project Structure

```
src/gh_score/
├── __init__.py              # Package entry point
├── __main__.py              # CLI entry point for `python -m gh_score`
├── config.py                # Configuration management (TOML + env vars)
├── core/
│   ├── api.py               # Main API orchestration
│   ├── cache.py             # Persistent filesystem cache with TTL
│   ├── models.py            # Data models (30+ dataclasses/enums)
│   ├── analyzers/
│   │   ├── release_health.py    # Release pattern analysis
│   │   ├── license_analyzer.py  # License classification
│   │   ├── contributors.py      # Contributor patterns & bus factor
│   │   ├── maintenance.py       # Maintenance state detection
│   │   ├── languages.py         # Language breakdown & ecosystem
│   │   └── sustainability.py    # Funding & backing detection
│   └── fetchers/
│       ├── github.py            # GitHub REST API fetcher
│       ├── local_git.py         # Local git repository analyzer
│       └── registries.py        # Package registry queries
├── cli/
│   ├── main.py              # Click CLI commands
│   └── tui.py               # Rich TUI dashboard renderer
└── llm/
    └── provider.py          # Optional LLM integration (Ollama/OpenAI)
```

## Features Implemented

### 1. Data Collection
- **GitHub API Fetcher**: Fetches metadata, releases, contributors, commits, issues, languages, community files
- **Local Git Fetcher**: Analyzes local clones without API calls
- **Package Registry Fetcher**: Detects and queries PyPI, npm, crates.io
- **Caching**: Filesystem-based cache with configurable TTL (24h default)

### 2. Analysis (6 Indicator Families)
- **Release Health**: Latest version, age, cadence, semver compliance
- **License**: SPDX ID, family classification (permissive/copyleft/public domain), OSI approval
- **Contributors**: Bus factor, bot ratio, lead detection, activity trends
- **Maintenance**: State classification (active/maintenance/abandoned), commit frequency, issue velocity
- **Languages**: Primary language, breakdown percentages, ecosystem inference
- **Sustainability**: Funding platforms, corporate backing, foundation membership

### 3. Output Formats
- **TUI Dashboard**: Rich terminal UI with color-coded status indicators
- **JSON**: Structured output for programmatic use
- **Markdown**: Human-readable reports

### 4. CLI Commands
```bash
gh-score [URL] [OPTIONS]     # Analyze a repository
gh-score config              # Show current configuration
gh-score report [URL]        # Generate detailed report
```

### 5. Configuration
- TOML config file support (`~/.config/gh-score/config.toml`)
- Environment variables (`GITHUB_TOKEN`, `GH_SCORE_*`)
- CLI flags override config file

### 6. LLM Integration (Optional)
- Ollama (default, local)
- OpenAI-compatible APIs
- Extracts qualitative signals from README/GOVERNANCE/SECURITY

## Testing

**42 unit tests** covering:
- Core models (RepoUrl, ReleaseHealth, LanguageBreakdown)
- All 6 analyzers
- Cache operations
- Configuration loading

All tests pass: `pytest tests/ -v`

## Usage Examples

### Remote Analysis
```bash
gh-score https://github.com/pallets/markupsafe
```

### Local Analysis
```bash
cd /path/to/repo
gh-score --local .
```

### With Options
```bash
gh-score --no-llm --format json https://github.com/owner/repo
gh-score --refresh https://github.com/owner/repo  # bypass cache
```

### Library Usage
```python
from gh_score import analyze_repo

result = analyze_repo("https://github.com/owner/repo")
print(result.release_health.latest_version)
print(result.contributors.bus_factor)
print(result.maintenance.state)
```

## Dependencies
- `httpx` - Async HTTP client
- `rich` - Terminal UI
- `click` - CLI framework
- `platformdirs` - Platform-specific directories
- `gitpython` - Local git operations

## Status Indicators
- ✓ **Healthy** (green) - Good health
- ⚠ **Warning** (yellow) - Needs attention
- ✗ **Critical** (red) - Significant issues
- ? **Unknown** (dim) - Insufficient data

## Implementation Notes
- All network calls are cached to respect API rate limits
- GitHub token optional (anonymous mode with 60 req/hour limit)
- LLM is opt-in and never blocks the analysis pipeline
- Local analysis works offline (no API calls for git data)
- Thread-safe cache implementation
