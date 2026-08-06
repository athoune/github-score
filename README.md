# gh-score

GitHub Project Health Scorer — evaluate maturity, maintenance, community health and sustainability of any GitHub project, in your terminal.

`gh-score` turns raw GitHub signals (commit activity, contributors, releases, license, registries, sustainability) into a single **traffic-light verdict**: 🟢 green (safe to bet on), 🟠 orange (proceed with caution), 🔴 red (risky).

## Usage

```bash
# Analyze a remote repository (no install needed)
uvx gh-score https://github.com/owner/repo

# Analyze the current directory (if it's a git clone)
uvx gh-score

# Or install it
pip install gh-score
gh-score https://github.com/owner/repo
```

### Output formats

```bash
gh-score --format tui       https://github.com/owner/repo   # terminal dashboard (default)
gh-score --format markdown  https://github.com/owner/repo   # Markdown report
gh-score --format json      https://github.com/owner/repo   # structured JSON
```

### Optional LLM analysis

The LLM is opt-in and never required. It extracts qualitative facts that
cannot be derived from APIs or files (roadmap, commercial support,
security policy, self-declared maintenance state) and produces a refined,
complementary recommendation that weighs all indicators together.

```bash
export GH_SCORE_LLM_ENABLED=true
export GH_SCORE_LLM_BASE_URL="https://api.openai.com/v1"   # any OpenAI-compatible API
export GH_SCORE_LLM_MODEL="gpt-4o-mini"
export GH_SCORE_LLM_API_KEY="sk-..."
gh-score https://github.com/owner/repo
```

### Configuration

Set a `GITHUB_TOKEN` to raise API rate limits. LLM settings can also live
in a `config.toml` (see `gh-score config`). Everything stays optional:
the tool works fully offline with local clones and no token.

## Library

```python
from gh_score import analyze_repo

result = analyze_repo("https://github.com/owner/repo")
print(result.recommendation.level)          # RecommendationLevel.GREEN
print(result.release_health.latest_version) # "v1.0.0"
print(result.contributors.bus_factor)       # 3
```

## License

GPL-3.0 — see [LICENSE](LICENSE).
