# LLM Qualitative Signals — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the optional LLM extract qualitative facts that are *inaccessible otherwise* (roadmap, security policy content, commercial support, self-declared maintenance state) and feed them into the report and the recommendation verdict.

**Architecture:** A new 5th indicator family (`QualitativeIndicator`) replaces the current display-only `llm_signals` dict. The LLM prompt is reduced to the four non-deterministic signals (sponsors and governance model are dropped: they already have deterministic regex implementations). The recommendation gains one hard-negative branch (text-declared abandonment, trusted only when commit data is missing) and two positive branches (data-poor "unknown" rescue, and a "roadmap + commercial support" message upgrade), all gated on the LLM being enabled. Confidence counts the qualitative indicator only when it ran.

**Tech Stack:** Python ≥ 3.12, httpx (OpenAI-compatible `/chat/completions`), Rich, pytest + pytest-asyncio. No new dependencies.

---

## Design decisions (validated)

1. **LLM stays optional** and disabled by default (`config.llm.enabled = false`).
2. **No LLM duplication of deterministic functions**: `sponsors` (regex `_detect_corporate_backing`) and `governance_model` (regex `_detect_governance_model`) are removed from the LLM prompt. LLM scope = `roadmap`, `security_policy`, `commercial_support`, `text_maintenance_state`.
3. **Verdict impact**:
   - *Negative*: text-declared abandonment → RED (ORANGE if widely used), **only when** `MaintenanceState.UNKNOWN` (commit data wins over text).
   - *Positive*: text "active" + roadmap or commercial support rescues the data-poor `UNKNOWN` branch (ORANGE → GREEN); roadmap + commercial support upgrades the plain green "active" message to "large community" (message only, no level change).
   - Positives never override hard data verdicts (abandoned, declining, bot-dominated, no stable release).
4. **OpenAI API is universal**: provider stays OpenAI-compatible (`/chat/completions`), configured via `base_url` + `api_key`.
5. **Docs must follow** (project AGENTS.md): `RULES.md` and `SPECS.md` updated in the same change as the code.

---

## Files touched

- Modify: `pyproject.toml` (pytest marker config)
- Modify: `src/gh_score/core/models.py`
- Modify: `src/gh_score/i18n.py`
- Modify: `src/gh_score/llm/provider.py`
- Create: `src/gh_score/core/analyzers/qualitative.py`
- Modify: `src/gh_score/core/api.py`
- Modify: `src/gh_score/core/analyzers/recommendation.py`
- Modify: `src/gh_score/cli/tui.py`
- Modify: `src/gh_score/cli/main.py`
- Modify: `src/gh_score/core/analyzers/sustainability.py` (stop reading `repo.llm_signals`)
- Tests: `tests/test_models.py`, `tests/test_provider.py` (new), `tests/test_qualitative.py` (new), `tests/test_recommendation.py`, `tests/test_api.py`, `tests/test_tui.py`, `tests/test_i18n.py`, `tests/test_llm_functional.py` (new)
- Docs: `RULES.md`, `SPECS.md`

---

### Task 0: pytest marker for functional LLM tests

**Files:**
- Modify: `pyproject.toml`

**Step 1: Write the failing config**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "llm: functional tests that call a real LLM (skipped unless GH_SCORE_LLM_ENABLED)",
]
```

**Step 2: Verify**

Run: `uv run pytest --markers | grep llm`
Expected: the marker is listed (no error).

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "test: declare llm pytest marker for functional tests"
```

---

### Task 1: Typed models — `QualitativeSignals` + `QualitativeIndicator`

**Files:**
- Modify: `src/gh_score/core/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test** (append to `tests/test_models.py`):

```python
from gh_score.core.models import QualitativeIndicator, QualitativeSignals


class TestQualitativeSignals:
    def test_empty_is_not_available(self):
        assert QualitativeSignals().any is False

    def test_any_signal_marks_available(self):
        s = QualitativeSignals(roadmap="v2 planned")
        assert s.any is True
        assert QualitativeSignals(text_maintenance_state="abandoned").any is True

    def test_indicator_defaults(self):
        ind = QualitativeIndicator()
        assert ind.available is False
        assert ind.status == Status.UNKNOWN
        assert ind.roadmap is None
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL — `QualitativeSignals` does not exist.

**Step 3: Implement** — in `models.py`:

Add a new section after the `SustainabilityIndicator` block (before `RecommendationLevel`):

```python
# ---------------------------------------------------------------------------
# Qualitative signals (optional LLM extraction)
# ---------------------------------------------------------------------------


@dataclass
class QualitativeSignals:
    """Qualitative facts extracted by the optional LLM from repository text.

    These are the only LLM-extracted signals: everything else already has a
    deterministic implementation. ``text_maintenance_state`` is the project's
    *self-declared* state as written in README/GOVERNANCE/SECURITY, one of
    "active", "maintenance", "abandoned", "unknown".
    """
    roadmap: str | None = None
    security_policy: str | None = None
    commercial_support: str | None = None
    text_maintenance_state: str | None = None

    @property
    def any(self) -> bool:
        """True when at least one signal was extracted (LLM actually ran)."""
        return any(
            v is not None
            for v in (self.roadmap, self.security_policy,
                      self.commercial_support, self.text_maintenance_state)
        )


@dataclass
class QualitativeIndicator:
    """5th indicator family: optional LLM qualitative facts.

    ``available`` is True when the LLM ran and returned at least one signal;
    it drives confidence accounting and gates the qualitative branches of the
    recommendation.
    """
    roadmap: str | None = None
    security_policy: str | None = None
    commercial_support: str | None = None
    text_maintenance_state: str | None = None
    available: bool = False
    status: Status = Status.UNKNOWN
    interpretation: str = ""
```

Replace the `Repository.llm_signals` field (currently `dict[str, str | list[str] | None]`, line ~267):

```python
    llm_signals: QualitativeSignals = field(default_factory=QualitativeSignals)
```

Remove `llm_signals` from `SustainabilityIndicator` (currently line ~365-366):

```python
@dataclass
class SustainabilityIndicator:
    has_funding: bool = False
    funding_platforms: list[str] = field(default_factory=list)
    corporate_backing: str | None = None
    foundation: str | None = None
    governance_model: str | None = None
    status: Status = Status.UNKNOWN
    interpretation: str = ""
```

Add `qualitative` to `AnalysisResult` (after `recommendation`):

```python
@dataclass
class AnalysisResult:
    url: RepoUrl
    meta: RepositoryMeta
    release_health: ReleaseHealthIndicator
    license: LicenseIndicator
    contributors: ContributorsIndicator
    maintenance: MaintenanceIndicator
    languages: LanguagesIndicator
    sustainability: SustainabilityIndicator
    registries: list[RegistryInfo] = field(default_factory=list)
    recommendation: Recommendation = field(default_factory=Recommendation)
    qualitative: QualitativeIndicator = field(default_factory=QualitativeIndicator)
```

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/gh_score/core/models.py tests/test_models.py
git commit -m "feat: add typed QualitativeSignals and QualitativeIndicator models"
```

---

### Task 2: Provider — narrow prompt + typed parsing

**Files:**
- Modify: `src/gh_score/llm/provider.py`
- Create: `tests/test_provider.py`

**Step 1: Write the failing test** (new file `tests/test_provider.py`):

```python
"""Tests for the LLM provider prompt and JSON parsing (no network)."""

from gh_score.core.models import QualitativeSignals
from gh_score.llm.provider import _parse_qualitative, _TEXT_MAINTENANCE_STATES


class TestParseQualitative:
    def test_full_signals(self):
        raw = {
            "roadmap": "v2 with async support",
            "security_policy": "report via GitHub private advisory",
            "commercial_support": "paid support available",
            "text_maintenance_state": "active",
        }
        s = _parse_qualitative(raw)
        assert s == QualitativeSignals(
            roadmap="v2 with async support",
            security_policy="report via GitHub private advisory",
            commercial_support="paid support available",
            text_maintenance_state="active",
        )

    def test_null_and_missing_fields(self):
        s = _parse_qualitative({"roadmap": None})
        assert s.any is False

    def test_invalid_maintenance_state_rejected(self):
        s = _parse_qualitative({"text_maintenance_state": "kinda alive"})
        assert s.text_maintenance_state is None

    def test_state_values(self):
        assert _TEXT_MAINTENANCE_STATES == {"active", "maintenance", "abandoned", "unknown"}


class TestPromptScope:
    """Regression: the LLM must not duplicate deterministic functions."""

    def test_prompt_excludes_sponsors_and_governance(self):
        from gh_score.llm.provider import _build_prompt

        prompt = _build_prompt("sustainability and governance")
        assert "sponsors" not in prompt
        assert "governance_model" not in prompt
        assert "roadmap" in prompt
        assert "text_maintenance_state" in prompt
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_provider.py -q`
Expected: FAIL — `_parse_qualitative` does not exist.

**Step 3: Implement** — in `llm/provider.py`:

Add module-level constants and helpers:

```python
# Allowed values for the self-declared maintenance state the LLM may report.
_TEXT_MAINTENANCE_STATES = frozenset({"active", "maintenance", "abandoned", "unknown"})

# Deliberately EXCLUDED from the prompt: sponsors/backers and governance
# model already have deterministic implementations (see sustainability.py).
_SIGNAL_FIELDS = (
    "roadmap: brief summary of any roadmap or future plans mentioned "
    "(or null)",
    "security_policy: brief summary of any security policy or vulnerability "
    "handling mentioned (or null)",
    "commercial_support: brief summary of any commercial support / paid "
    "services offered (or null)",
    "text_maintenance_state: the project's self-declared maintenance state, "
    'one of "active", "maintenance", "abandoned", "unknown" (or null)',
)


def _build_prompt(context: str) -> str:
    fields = "\n".join(f"- {f}" for f in _SIGNAL_FIELDS)
    return (
        f"Analyze the following text about a software project and extract "
        f"information about {context}.\n\n"
        "Return a JSON object with these fields (null when not mentioned):\n"
        f"{fields}\n\n"
        "Text:\n{text}\n\n"
        "Return only valid JSON, no markdown formatting."
    )


def _clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _parse_qualitative(data: dict) -> QualitativeSignals:
    """Map the LLM JSON payload onto typed QualitativeSignals."""
    state = data.get("text_maintenance_state")
    if state not in _TEXT_MAINTENANCE_STATES:
        state = None
    return QualitativeSignals(
        roadmap=_clean_str(data.get("roadmap")),
        security_policy=_clean_str(data.get("security_policy")),
        commercial_support=_clean_str(data.get("commercial_support")),
        text_maintenance_state=state,
    )
```

Rename `analyze_sustainability_with_llm` → `analyze_qualitative_with_llm`, return `QualitativeSignals`, use the new prompt and parser:

```python
async def analyze_qualitative_with_llm(
    repo, config: LLMConfig
) -> QualitativeSignals:
    """Use the LLM to extract qualitative facts from repository text.

    Scope is limited to signals with NO deterministic implementation:
    roadmap, security policy content, commercial support, and the
    project's self-declared maintenance state. Returns empty signals when
    the LLM is disabled, has no text to analyze, or fails.
    """
    if not config.enabled:
        return QualitativeSignals()

    texts = []
    if repo.readme_content:
        texts.append(f"README:\n{repo.readme_content[:2000]}")
    if repo.governance_content:
        texts.append(f"GOVERNANCE:\n{repo.governance_content[:1500]}")
    if repo.security_content:
        texts.append(f"SECURITY:\n{repo.security_content[:1000]}")
    if not texts:
        return QualitativeSignals()

    combined_text = "\n\n".join(texts)

    provider = LLMProvider(config)
    try:
        prompt = _build_prompt("sustainability and governance")
        # extract_signals must interpolate the text; keep its signature by
        # passing the prompt template — see Step 3 note.
        raw = await provider.extract_signals(prompt, combined_text)
        return _parse_qualitative(raw)
    except Exception:
        return QualitativeSignals()
    finally:
        await provider.close()
```

**Step 3 note — `extract_signals` signature.** The current `extract_signals(text, context)` builds the prompt internally. Change it to accept the fully built prompt to keep `_build_prompt` testable and single-sourced:

```python
async def extract_signals(self, prompt: str, text: str) -> dict[str, Any]:
    """Ask the LLM to extract structured signals.

    Args:
        prompt: The full instruction (see _build_prompt).
        text: The repository text excerpts to analyze.

    Returns:
        Parsed JSON dict, or {} on any failure (LLM is optional).
    """
    if not self.enabled:
        return {}

    filled = prompt.replace("{text}", text)

    try:
        response = await self.client.post(
            "/chat/completions",
            json={
                "model": self.config.model,
                "messages": [{"role": "user", "content": filled}],
                "temperature": 0.3,
                "max_tokens": 500,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        result = json.loads(content.strip())
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}
```

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_provider.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/gh_score/llm/provider.py tests/test_provider.py
git commit -m "feat: narrow LLM prompt to non-deterministic signals with typed parsing"
```

---

### Task 3: Qualitative analyzer

**Files:**
- Create: `src/gh_score/core/analyzers/qualitative.py`
- Create: `tests/test_qualitative.py`

**Step 1: Write the failing test** (new file `tests/test_qualitative.py`):

```python
"""Tests for the qualitative (LLM) analyzer."""

from gh_score.core.analyzers.qualitative import analyze_qualitative
from gh_score.core.models import (
    QualitativeSignals,
    Repository,
    RepoUrl,
    Status,
)


def _repo(signals: QualitativeSignals | None = None) -> Repository:
    repo = Repository(url=RepoUrl("owner", "repo"))
    repo.llm_signals = signals or QualitativeSignals()
    return repo


class TestAnalyzeQualitative:
    def test_no_signals_not_available(self):
        ind = analyze_qualitative(_repo())
        assert ind.available is False
        assert ind.status == Status.UNKNOWN

    def test_signals_mapped_and_available(self):
        ind = analyze_qualitative(_repo(QualitativeSignals(
            roadmap="v2",
            text_maintenance_state="active",
        )))
        assert ind.available is True
        assert ind.status == Status.HEALTHY
        assert ind.roadmap == "v2"
        assert ind.text_maintenance_state == "active"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_qualitative.py -q`
Expected: FAIL — module `qualitative` does not exist.

**Step 3: Implement** — new file `src/gh_score/core/analyzers/qualitative.py`:

```python
"""Qualitative signals analyzer.

Maps the optional LLM-extracted facts (roadmap, security policy content,
commercial support, self-declared maintenance state) onto a typed
indicator. The indicator is ``available`` only when the LLM actually ran
and returned at least one signal; this drives confidence accounting and
gates the qualitative branches of the recommendation.
"""

from __future__ import annotations

from gh_score.core.models import (
    QualitativeIndicator,
    Repository,
    Status,
)
from gh_score.i18n import t


def analyze_qualitative(repo: Repository) -> QualitativeIndicator:
    """Build the QualitativeIndicator from repository LLM signals."""
    signals = repo.llm_signals
    available = bool(signals and signals.any)

    indicator = QualitativeIndicator(
        roadmap=signals.roadmap if available else None,
        security_policy=signals.security_policy if available else None,
        commercial_support=signals.commercial_support if available else None,
        text_maintenance_state=signals.text_maintenance_state if available else None,
        available=available,
        status=Status.HEALTHY if available else Status.UNKNOWN,
    )

    if available:
        indicator.interpretation = _build_interpretation(indicator)
    return indicator


def _build_interpretation(ind: QualitativeIndicator) -> str:
    parts = []
    if ind.roadmap:
        parts.append(t("int_roadmap", text=ind.roadmap))
    if ind.commercial_support:
        parts.append(t("int_commercial", text=ind.commercial_support))
    if ind.security_policy:
        parts.append(t("int_security", text=ind.security_policy))
    if ind.text_maintenance_state:
        parts.append(t("int_text_state", state=t(f"state_{ind.text_maintenance_state}")))
    return ", ".join(parts)
```

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_qualitative.py -q`
Expected: PASS (i18n keys added in Task 4).

**Step 5: Commit**

```bash
git add src/gh_score/core/analyzers/qualitative.py tests/test_qualitative.py
git commit -m "feat: add qualitative (LLM) indicator analyzer"
```

---

### Task 4: i18n keys

**Files:**
- Modify: `src/gh_score/i18n.py`
- Test: `tests/test_i18n.py`

**Step 1: Write the failing test** (append to `tests/test_i18n.py`):

```python
def test_qualitative_keys_present():
    from gh_score.i18n import MESSAGES

    for lang in ("fr", "en"):
        for key in (
            "rec_text_discontinued", "reason_text_discontinued",
            "fact_roadmap", "fact_commercial", "fact_security",
            "int_roadmap", "int_commercial", "int_security", "int_text_state",
            "tui_qualitative", "tui_roadmap", "tui_security",
            "tui_commercial", "tui_text_state",
            "md_section_qualitative", "md_roadmap", "md_security",
            "md_commercial", "md_text_state",
        ):
            assert key in MESSAGES[lang], f"{lang}:{key} missing"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_i18n.py -q`
Expected: FAIL — keys missing.

**Step 3: Implement** — add to both catalogs in `i18n.py`:

French (`fr`):

```python
        "rec_text_discontinued": "Les textes du projet annoncent son abandon",
        "reason_text_discontinued": (
            "le README/GOVERNANCE déclare explicitement que le projet n'est "
            "plus maintenu"
        ),
        "fact_roadmap": "feuille de route annoncée",
        "fact_commercial": "support commercial disponible",
        "fact_security": "politique de sécurité documentée",
        "int_roadmap": "roadmap : {text}",
        "int_commercial": "support commercial : {text}",
        "int_security": "sécurité : {text}",
        "int_text_state": "état déclaré : {state}",
        "panel_qualitative": "Signaux qualitatifs",
        "tui_roadmap": "roadmap : {text}",
        "tui_security": "sécurité : {text}",
        "tui_commercial": "support commercial : {text}",
        "tui_text_state": "état déclaré : {state}",
        "md_section_qualitative": "## Signaux qualitatifs",
        "md_roadmap": "**Roadmap :** {text}",
        "md_security": "**Sécurité :** {text}",
        "md_commercial": "**Support commercial :** {text}",
        "md_text_state": "**État déclaré :** {state}",
```

English (`en`):

```python
        "rec_text_discontinued": "Project texts announce its discontinuation",
        "reason_text_discontinued": (
            "README/GOVERNANCE explicitly states the project is no longer "
            "maintained"
        ),
        "fact_roadmap": "roadmap announced",
        "fact_commercial": "commercial support available",
        "fact_security": "security policy documented",
        "int_roadmap": "roadmap: {text}",
        "int_commercial": "commercial support: {text}",
        "int_security": "security: {text}",
        "int_text_state": "declared state: {state}",
        "panel_qualitative": "Qualitative Signals",
        "tui_roadmap": "roadmap: {text}",
        "tui_security": "security: {text}",
        "tui_commercial": "commercial support: {text}",
        "tui_text_state": "declared state: {state}",
        "md_section_qualitative": "## Qualitative Signals",
        "md_roadmap": "**Roadmap:** {text}",
        "md_security": "**Security:** {text}",
        "md_commercial": "**Commercial support:** {text}",
        "md_text_state": "**Declared state:** {state}",
```

Note: `int_roadmap`/`int_commercial`/`int_security` use `{text}` — the analyzer already passes `text=` (Task 3).

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_i18n.py tests/test_qualitative.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/gh_score/i18n.py src/gh_score/core/analyzers/qualitative.py tests/test_i18n.py
git commit -m "feat: add i18n keys for qualitative signals"
```

---

### Task 5: Wire into the pipeline

**Files:**
- Modify: `src/gh_score/core/api.py`
- Modify: `src/gh_score/core/analyzers/sustainability.py`
- Test: `tests/test_api.py`

**Step 1: Write the failing test** — update `TestLlmIntegration` in `tests/test_api.py`:

```python
class TestLlmIntegration:
    @pytest.mark.asyncio
    async def test_signals_attached_when_enabled(self, tmp_path):
        config = _make_config(tmp_path)
        config.llm.enabled = True
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(return_value=QualitativeSignals(
                    roadmap="v2 planned",
                    text_maintenance_state="active",
                )),
            ) as mock_llm,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        mock_llm.assert_awaited_once()
        assert result.qualitative.available is True
        assert result.qualitative.roadmap == "v2 planned"

    @pytest.mark.asyncio
    async def test_not_called_when_disabled(self, tmp_path):
        config = _make_config(tmp_path)  # llm.enabled = False
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "gh_score.core.api.analyze_qualitative_with_llm",
                new=AsyncMock(return_value=QualitativeSignals()),
            ) as mock_llm,
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            await analyze_repo_async("https://github.com/owner/repo", config)

        mock_llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_qualitative_always_present(self, tmp_path):
        config = _make_config(tmp_path)  # llm disabled
        repo = _make_repo_data()

        with (
            patch("gh_score.core.api.GitHubFetcher") as mock_fetcher_cls,
            patch(
                "gh_score.core.api.fetch_registry_info",
                new=AsyncMock(return_value=[]),
            ),
        ):
            _mock_fetcher(mock_fetcher_cls, repo)
            result = await analyze_repo_async("https://github.com/owner/repo", config)

        assert result.qualitative.available is False
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL — `analyze_qualitative_with_llm` not imported; `qualitative` attribute missing.

**Step 3: Implement**

`src/gh_score/core/api.py`:

```python
from gh_score.core.analyzers import (
    analyze_contributors,
    analyze_languages,
    analyze_license,
    analyze_maintenance,
    analyze_qualitative,
    analyze_recommendation,
    analyze_release_health,
    analyze_sustainability,
)
...
from gh_score.llm.provider import analyze_qualitative_with_llm
```

Replace the LLM block (currently lines ~86-91):

```python
    # Optional LLM analysis for qualitative signals
    if config.llm.enabled:
        repo.llm_signals = await analyze_qualitative_with_llm(repo, config.llm)
```

Add to the `AnalysisResult` construction:

```python
        sustainability=analyze_sustainability(repo),
        qualitative=analyze_qualitative(repo),
        registries=repo.registries,
```

`src/gh_score/core/analyzers/sustainability.py`: remove the `repo.llm_signals` handling — delete lines 171-172 and the `llm_signals=llm_signals,` argument in `SustainabilityIndicator(...)`.

Register `analyze_qualitative` in `src/gh_score/core/analyzers/__init__.py`.

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/gh_score/core/api.py src/gh_score/core/analyzers/sustainability.py src/gh_score/core/analyzers/__init__.py tests/test_api.py
git commit -m "feat: wire LLM qualitative signals into the analysis pipeline"
```

---

### Task 6: Recommendation branches + confidence

**Files:**
- Modify: `src/gh_score/core/analyzers/recommendation.py`
- Test: `tests/test_recommendation.py`

**Step 1: Write the failing tests** (append to `tests/test_recommendation.py`):

Add `qualitative` support to `_make_result`:

```python
def _make_result(
    *,
    ...,
    qualitative: QualitativeIndicator | None = None,
) -> AnalysisResult:
    ...
    return AnalysisResult(
        ...
        registries=registries or [],
        qualitative=qualitative or QualitativeIndicator(),
    )
```

New tests:

```python
class TestQualitativeSignals:
    def test_text_abandonment_is_red_when_data_missing(self):
        result = _make_result(
            state=MaintenanceState.UNKNOWN,
            stars=50,
            qualitative=QualitativeIndicator(
                text_maintenance_state="abandoned",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.RED
        assert "abandon" in rec.message

    def test_text_abandonment_widely_used_is_orange(self):
        result = _make_result(
            state=MaintenanceState.UNKNOWN,
            stars=20_000,
            qualitative=QualitativeIndicator(
                text_maintenance_state="abandoned",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE

    def test_text_abandonment_ignored_when_data_says_active(self):
        # Commit data wins over text.
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            latest_version="v1.0.0",
            qualitative=QualitativeIndicator(
                text_maintenance_state="abandoned",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.GREEN

    def test_text_active_rescues_data_poor_project(self):
        result = _make_result(
            state=MaintenanceState.UNKNOWN,
            stars=10,
            qualitative=QualitativeIndicator(
                text_maintenance_state="active",
                roadmap="v2",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.GREEN
        assert rec.message == "Projet actif"

    def test_text_active_without_roadmap_does_not_rescue(self):
        result = _make_result(
            state=MaintenanceState.UNKNOWN,
            stars=10,
            qualitative=QualitativeIndicator(
                text_maintenance_state="active",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "insuffisantes" in rec.message

    def test_roadmap_and_commercial_upgrade_active_message(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            total_authors=5,
            latest_version="v1.0.0",
            qualitative=QualitativeIndicator(
                roadmap="v2",
                commercial_support="paid tiers",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.GREEN
        assert "grande communauté" in rec.message

    def test_disabled_llm_has_no_effect(self):
        # available=False → all qualitative branches are skipped.
        result = _make_result(
            state=MaintenanceState.UNKNOWN,
            stars=10,
            qualitative=QualitativeIndicator(
                text_maintenance_state="abandoned",
                roadmap="v2",
                available=False,
            ),
        )
        rec = _recommend(result)
        assert rec.level == RecommendationLevel.ORANGE
        assert "insuffisantes" in rec.message

    def test_reasoning_mentions_qualitative_facts(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            total_authors=5,
            latest_version="v1.0.0",
            qualitative=QualitativeIndicator(
                roadmap="v2",
                commercial_support="paid tiers",
                security_policy="private advisory",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert any("feuille de route" in r for r in rec.reasoning)
        assert any("support commercial" in r for r in rec.reasoning)
        assert any("politique de sécurité" in r for r in rec.reasoning)

    def test_confidence_counts_qualitative_when_available(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            total_authors=5,
            latest_version="v1.0.0",
            qualitative=QualitativeIndicator(
                roadmap="v2",
                available=True,
                status=Status.HEALTHY,
            ),
        )
        rec = _recommend(result)
        assert rec.confidence == 1.0  # 5/5 known

    def test_confidence_unaffected_when_llm_disabled(self):
        result = _make_result(
            state=MaintenanceState.ACTIVE,
            stars=200,
            total_authors=5,
            latest_version="v1.0.0",
        )
        rec = _recommend(result)
        assert rec.confidence == 1.0  # still 4/4 known
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_recommendation.py -q`
Expected: FAIL — new branches not implemented.

**Step 3: Implement** — `recommendation.py`:

Add a helper:

```python
def _qualitative_available(result: AnalysisResult) -> bool:
    return result.qualitative.available
```

In `analyze_recommendation`, before the "Unknown maintenance state" section (insert after the maintenance-mode block):

```python
    # 5.5 LLM-reported discontinuation. Trusted only when commit data is
    #     missing: explicit text ("no longer maintained") is a fact, but
    #     actual commit activity wins over prose.
    q = result.qualitative
    if (
        q.available
        and maint.state == MaintenanceState.UNKNOWN
        and q.text_maintenance_state == "abandoned"
    ):
        if _is_widely_used(result):
            return _build(
                RecommendationLevel.ORANGE,
                t("rec_abandoned_popular", lang=lang),
                result,
                lang,
                t("reason_text_discontinued", lang=lang),
            )
        return _build(
            RecommendationLevel.RED,
            t("rec_text_discontinued", lang=lang),
            result,
            lang,
            t("reason_text_discontinued", lang=lang),
        )
```

In the "Unknown maintenance state" section:

```python
    # 6. Unknown maintenance state.
    if _is_widely_used(result):
        return _build(
            RecommendationLevel.ORANGE,
            t("rec_widely_used_unmaintained", lang=lang),
            result,
            lang,
            t("reason_unknown_widely_used", lang=lang),
        )
    if (
        q.available
        and q.text_maintenance_state == "active"
        and (q.roadmap or q.commercial_support)
    ):
        # Commit data is missing, but the text declares active development
        # with a direction (roadmap or commercial support).
        return _build(
            RecommendationLevel.GREEN,
            t("rec_active", lang=lang),
            result,
            lang,
            t("reason_active", lang=lang),
            t("reason_text_active", lang=lang),
        )
    return _build(
        RecommendationLevel.ORANGE,
        t("rec_insufficient_data", lang=lang),
        result,
        lang,
        t("reason_insufficient", lang=lang),
    )
```

In the ACTIVE branch, upgrade the large-community check:

```python
        if _has_large_community(result) or (
            q.available and q.roadmap and q.commercial_support
        ):
            return _build(
                RecommendationLevel.GREEN,
                t("rec_active_community", lang=lang),
                result,
                lang,
                t("reason_active", lang=lang),
            )
```

In `_build`, append qualitative facts:

```python
    if result.qualitative.available:
        if result.qualitative.roadmap:
            reasoning.append(t("fact_roadmap", lang=lang))
        if result.qualitative.commercial_support:
            reasoning.append(t("fact_commercial", lang=lang))
        if result.qualitative.security_policy:
            reasoning.append(t("fact_security", lang=lang))
```

Update `_compute_confidence` to count the qualitative indicator only when available:

```python
def _compute_confidence(result: AnalysisResult) -> float:
    known = 0
    total = 0
    for indicator in (
        result.maintenance,
        result.contributors,
        result.release_health,
        result.sustainability,
    ):
        total += 1
        if indicator.status != Status.UNKNOWN:
            known += 1
    if result.qualitative.available:
        total += 1
        if result.qualitative.status != Status.UNKNOWN:
            known += 1
    if total == 0:
        return 0.0
    return round(known / total, 2)
```

Add i18n key `reason_text_active` (both catalogs):

```python
        "reason_text_active": "le texte du projet déclare un développement actif",
        # en: "the project text declares active development"
```

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_recommendation.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/gh_score/core/analyzers/recommendation.py src/gh_score/i18n.py tests/test_recommendation.py
git commit -m "feat: use LLM qualitative signals in recommendation and confidence"
```

---

### Task 7: Report rendering (TUI + Markdown)

**Files:**
- Modify: `src/gh_score/cli/tui.py`
- Modify: `src/gh_score/cli/main.py`
- Test: `tests/test_tui.py`

**Step 1: Write the failing test** (append to `tests/test_tui.py`):

```python
class TestQualitativePanel:
    def test_panel_shows_signals(self, result, en_locale):
        result.qualitative = QualitativeIndicator(
            roadmap="v2 planned",
            commercial_support="paid tiers",
            security_policy="private advisory",
            text_maintenance_state="active",
            available=True,
            status=Status.HEALTHY,
        )
        panel = _render_qualitative(result)
        assert panel.title == "Qualitative Signals"
        text = _panel_text(panel)
        assert "roadmap: v2 planned" in text
        assert "commercial support: paid tiers" in text
        assert "security: private advisory" in text
        assert "declared state: active" in text

    def test_panel_hidden_when_not_available(self, result, en_locale):
        assert _render_qualitative(result) is None
```

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_tui.py -q`
Expected: FAIL — `_render_qualitative` does not exist.

**Step 3: Implement** — `tui.py`:

```python
def _render_qualitative(result: AnalysisResult) -> Panel | None:
    """Render the optional LLM qualitative signals panel."""
    q = result.qualitative
    if not q.available:
        return None

    glyph, color = _status_glyph(q.status)
    content = Text()
    content.append(f"{glyph} ", style=color)

    if q.roadmap:
        content.append(f"{t('tui_roadmap', text=q.roadmap[:80])}\n")
    if q.commercial_support:
        content.append(f"{t('tui_commercial', text=q.commercial_support[:80])}\n")
    if q.security_policy:
        content.append(f"{t('tui_security', text=q.security_policy[:80])}\n")
    if q.text_maintenance_state:
        content.append(
            f"{t('tui_text_state', state=t(f'state_{q.text_maintenance_state}'))}\n"
        )

    if q.interpretation:
        content.append(f"\n{q.interpretation}", style="dim")

    return Panel(content, title=t("panel_qualitative"), border_style=color)
```

In `render_dashboard`, print the panel after the sustainability grid:

```python
    console.print(grid2)

    qualitative_panel = _render_qualitative(result)
    if qualitative_panel:
        console.print(qualitative_panel)
```

Remove the now-dead `llm_signals` block from `_render_sustainability` (lines ~264-282).

Markdown — `main.py`, add a section renderer and call it from `_render_markdown`:

```python
def _md_qualitative(result: AnalysisResult, console: Console) -> None:
    q = result.qualitative
    if not q.available:
        return
    console.print(f"{t('md_section_qualitative')}\n")
    if q.text_maintenance_state:
        console.print(
            f"- {t('md_text_state', state=t(f'state_{q.text_maintenance_state}'))}"
        )
    if q.roadmap:
        console.print(f"- {t('md_roadmap', text=q.roadmap)}")
    if q.commercial_support:
        console.print(f"- {t('md_commercial', text=q.commercial_support)}")
    if q.security_policy:
        console.print(f"- {t('md_security', text=q.security_policy)}")
    console.print(f"- {t('md_status', status=t(f'status_{q.status.value}'))}\n")
```

Call it in `_render_markdown` after `_md_sustainability`.

**Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_tui.py -q`
Expected: PASS. (Also fix the TUI fixture: remove `llm_signals`-related fields if any remain, and the existing `TestPanels.test_sustainability` assertions must not reference LLM lines anymore.)

**Step 5: Commit**

```bash
git add src/gh_score/cli/tui.py src/gh_score/cli/main.py tests/test_tui.py
git commit -m "feat: render LLM qualitative signals in TUI and Markdown reports"
```

---

### Task 8: Functional LLM tests (skipped without an LLM)

**Files:**
- Create: `tests/test_llm_functional.py`
- Modify: `tests/conftest.py` (create if missing)

**Step 1: Write the skip helper** (`tests/conftest.py`):

```python
"""Shared fixtures for gh-score tests."""

import os

import pytest


def _llm_configured() -> bool:
    return os.environ.get("GH_SCORE_LLM_ENABLED", "").lower() in ("1", "true", "yes")


requires_llm = pytest.mark.skipif(
    not _llm_configured(),
    reason="Functional LLM tests need GH_SCORE_LLM_ENABLED=true and a reachable provider",
)
```

**Step 2: Write the failing test** (new file `tests/test_llm_functional.py`):

```python
"""Functional tests exercising a real LLM provider.

Skipped unless GH_SCORE_LLM_ENABLED is truthy. The user provides the
connection settings (base URL, model, key) via config or env:
GH_SCORE_LLM_ENABLED, GH_SCORE_LLM_BASE_URL, GH_SCORE_LLM_MODEL,
GH_SCORE_LLM_API_KEY.
"""

import pytest

from gh_score.config import LLMConfig
from gh_score.core.models import QualitativeSignals, Repository, RepoUrl
from gh_score.llm.provider import analyze_qualitative_with_llm

from conftest import requires_llm  # via tests/conftest.py


@requires_llm
@pytest.mark.asyncio
async def test_real_llm_extracts_qualitative_signals():
    config = LLMConfig(
        enabled=True,
        base_url=os.environ.get("GH_SCORE_LLM_BASE_URL", "http://localhost:11434/v1"),
        model=os.environ.get("GH_SCORE_LLM_MODEL", "llama3.2"),
        api_key=os.environ.get("GH_SCORE_LLM_API_KEY", ""),
    )

    repo = Repository(url=RepoUrl("owner", "repo"))
    repo.readme_content = (
        "# demo\n\n"
        "## Roadmap\nWe plan a v2 with async support.\n\n"
        "## Security\nVulnerabilities are handled via GitHub private advisories.\n\n"
        "This project is actively maintained.\n"
    )

    signals = await analyze_qualitative_with_llm(repo, config)

    assert isinstance(signals, QualitativeSignals)
    assert signals.any is True
    assert signals.text_maintenance_state in ("active", "maintenance", None)
    # The LLM must not invent fields we removed from the prompt schema.
    assert not hasattr(signals, "sponsors")
```

**Step 3: Run to verify it is skipped without an LLM**

Run: `uv run pytest tests/test_llm_functional.py -q`
Expected: `1 skipped` (or PASS if `GH_SCORE_LLM_ENABLED` is set and a provider answers).

**Step 4: Commit**

```bash
git add tests/conftest.py tests/test_llm_functional.py
git commit -m "test: add functional LLM tests skipped without a provider"
```

---

### Task 9: Docs — RULES.md and SPECS.md (required by AGENTS.md)

**Files:**
- Modify: `RULES.md`
- Modify: `SPECS.md`

**Step 1: RULES.md — decision tree**

Add after branch 5 (Maintenance mode), renumbering note-free:

```markdown
6. **LLM-reported discontinuation** (only when the LLM is enabled and the
   maintenance state is unknown — commit data wins over prose)
   - Widely used → 🟠 "Large project, but now abandoned"
   - Otherwise → 🔴 "Project texts announce its discontinuation"
```

Amend the **Unknown maintenance state** section:

```markdown
8. **Unknown maintenance state**
   - Widely used → 🟠 "Widely used project despite low maintenance"
   - LLM enabled, text declares active development AND (roadmap or
     commercial support) → 🟢 "Active project"
   - Otherwise → 🟠 "Insufficient data for a reliable recommendation"
```

Amend the **Active development** section's large-community line:

```markdown
   - Large community (≥ 100 human authors or ≥ 10k stars), or LLM-enabled
     with roadmap AND commercial support → 🟢 "Active project with a large community"
```

Amend the **Reasoning** section: objective facts now include LLM qualitative
facts (roadmap, commercial support, security policy, declared state).

Add to the **Message catalog** table: `rec_text_discontinued`,
`reason_text_discontinued`, `fact_roadmap`, `fact_commercial`,
`fact_security`, `reason_text_active`.

**Step 2: SPECS.md — section 8.3 responsibilities**

Replace the four bullet responsibilities:

```markdown
The LLM is given short text excerpts (README, GOVERNANCE, SECURITY) and
asked to return structured JSON limited to signals with **no deterministic
implementation**:

- Roadmap / future plans.
- Security policy content (file presence is detected deterministically).
- Commercial support offering.
- Self-declared maintenance state (`active` / `maintenance` / `abandoned` /
  `unknown`).

Sponsors/backers and the governance model are deliberately **not** asked of
the LLM: they already have deterministic regex implementations.

The LLM must never produce the final health verdict. Its outputs are
signals fed into the decision tree (gated on `llm.enabled`) and the report.
```

Also mirror the decision-tree additions under section 7.7.

**Step 3: Verify**

Run: `uv run pytest -q`
Expected: all pass (except the pre-existing environment-dependent
`tests/test_config.py::test_load_from_toml` failure caused by a real
`GITHUB_TOKEN` env var — unrelated).

Run: `uv run ruff check src tests`
Expected: clean.

**Step 4: Commit**

```bash
git add RULES.md SPECS.md
git commit -m "docs: document LLM qualitative signals in scoring rules"
```

---

## Verification checklist

- [ ] `uv run pytest -q` — green (minus the known env-dependent config test)
- [ ] `uv run ruff check src tests` — clean
- [ ] `uv run pytest tests/test_llm_functional.py -q` — skipped without LLM
- [ ] Manual smoke: `gh-score --no-llm --format markdown https://github.com/owner/repo` shows no qualitative section; with `llm.enabled = true` and a reachable provider, the section appears.
- [ ] RULES.md decision tree matches recommendation.py exactly.
