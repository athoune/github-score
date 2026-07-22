# gh-score

GitHub Project Health Scorer — evaluate maturity, maintenance, community health and sustainability of any GitHub project.

## Installation

```bash
pip install gh-score
```

## Usage

```bash
# Analyze a remote repository
gh-score https://github.com/owner/repo

# Analyze the current directory (if it's a git clone)
gh-score
```

## Library

```python
from gh_score import analyze_repo

result = analyze_repo("https://github.com/owner/repo")
print(result.release_health.latest_version)
print(result.contributors.bus_factor)
```
