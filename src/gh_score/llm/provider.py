"""LLM provider for optional qualitative analysis.

Supports Ollama (default) and OpenAI-compatible APIs.

The OpenAI chat-completions API is treated as the universal interface:
any provider exposing ``/chat/completions`` works (Ollama, OpenAI,
Azure, Gemini, llama.cpp, …), configured via ``base_url`` + ``api_key``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from gh_score.config import LLMConfig
from gh_score.core.models import LLMRecommendation, QualitativeSignals


# Allowed values for the self-declared maintenance state the LLM may report.
_TEXT_MAINTENANCE_STATES = frozenset({"active", "maintenance", "abandoned", "unknown"})

# Allowed traffic-light levels for the refined LLM recommendation.
_LLM_LEVELS = frozenset({"green", "orange", "red"})


class LLMError(Exception):
    """The LLM provider is unreachable or returned invalid output.

    Raised by :meth:`LLMProvider.extract_signals`; the public analysis
    helpers catch it and surface a warning instead of failing the pipeline.
    """

# Deliberately EXCLUDED from the prompt: sponsors/backers and the
# governance model already have deterministic implementations
# (see analyzers/sustainability.py). The LLM only extracts facts that
# cannot be derived from APIs or local files.
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
    """Build the instruction prompt (``{text}`` is filled by the provider)."""
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
    """Return a trimmed non-empty string, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _extract_json_object(content: str) -> dict:
    """Parse a JSON object out of LLM content, tolerating surrounding prose.

    Local models frequently wrap the answer in explanations. We try, in
    order: direct parse, fenced code blocks, then the first ``{`` to the
    last ``}`` in the text.
    """
    content = content.strip()

    try:
        result = json.loads(content)
        return result if isinstance(result, dict) else {}
    except ValueError:
        pass

    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0]

    try:
        result = json.loads(content.strip())
        return result if isinstance(result, dict) else {}
    except ValueError:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end > start:
        try:
            result = json.loads(content[start : end + 1])
            return result if isinstance(result, dict) else {}
        except ValueError:
            pass

    return {}


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


def _build_report_digest(result) -> dict[str, Any]:
    """Compact digest of the analysis result for the LLM prompt.

    Carries the key facts of every indicator family so the LLM can weigh
    them together. Values are plain JSON-friendly primitives.
    """
    meta = result.meta
    maint = result.maintenance
    contrib = result.contributors
    rh = result.release_health
    return {
        "owner": meta.owner,
        "owner_type": meta.owner_type or "unknown",
        "stars": meta.stars,
        "forks": meta.forks,
        "description": (meta.description or "")[:200],
        "maintenance": {
            "state": maint.state.value,
            "last_commit_days_ago": maint.last_commit_days_ago,
            "commits_per_month": maint.commits_per_month,
        },
        "contributors": {
            "authors": contrib.total_authors,
            "bus_factor": contrib.bus_factor,
            "bot_ratio": round(contrib.bot_ratio, 2),
            "activity_trend": contrib.activity_trend,
        },
        "release": {
            "latest_version": rh.latest_version,
            "age_days": rh.age_days,
            "cadence_days": rh.cadence_days,
        },
        "license": result.license.spdx_id,
        "primary_language": result.languages.primary if result.languages else None,
        "sustainability": {
            "has_funding": result.sustainability.has_funding,
            "funding_platforms": result.sustainability.funding_platforms,
            "corporate_backing": result.sustainability.corporate_backing,
            "foundation": result.sustainability.foundation,
            "governance_model": result.sustainability.governance_model,
        },
        "qualitative": {
            "roadmap": result.qualitative.roadmap,
            "commercial_support": result.qualitative.commercial_support,
            "security_policy": result.qualitative.security_policy,
            "text_maintenance_state": result.qualitative.text_maintenance_state,
        },
        "registries": [
            {
                "ecosystem": reg.ecosystem,
                "exists": reg.exists,
                "latest_version": reg.latest_version,
                "downloads": reg.downloads,
                "deprecated": reg.deprecated,
            }
            for reg in result.registries
        ],
    }


def _build_recommendation_prompt() -> str:
    """Instruction for the refined recommendation (``{digest}`` is filled
    with the JSON report digest by the caller)."""
    return (
        "You are evaluating whether a developer should bet on a GitHub "
        "project.\n\n"
        "Here is the analysis digest of the project, followed by the "
        "deterministic traffic-light verdict:\n"
        "{digest}\n\n"
        "Weigh all the information together (maintenance, contributors, "
        "releases, license, sustainability, qualitative signals) and produce "
        "a nuanced recommendation that may agree with, or refine, the "
        "deterministic verdict.\n\n"
        "Return a JSON object with:\n"
        '- level: one of "green", "orange", "red"\n'
        "- message: a short verdict sentence (max 15 words)\n"
        "- explanation: 2-4 sentences weighing the strongest signals and "
        "trade-offs\n"
        "- confidence: a number between 0 and 1 expressing your confidence\n\n"
        "Return only valid JSON, no markdown formatting."
    )


def _parse_recommendation(data: dict) -> LLMRecommendation:
    """Map the LLM JSON payload onto a typed LLMRecommendation."""
    level = data.get("level")
    if level not in _LLM_LEVELS:
        level = ""
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return LLMRecommendation(
        level=level,
        message=_clean_str(data.get("message")) or "",
        explanation=_clean_str(data.get("explanation")) or "",
        confidence=confidence,
    )


class LLMProvider:
    """Abstract LLM provider with OpenAI-compatible API."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.enabled = config.enabled

        if not self.enabled:
            return

        headers = {
            "Content-Type": "application/json",
        }
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            headers=headers,
            timeout=60.0,
        )

    async def close(self) -> None:
        if self.enabled:
            await self.client.aclose()

    async def extract_signals(
        self, prompt: str, max_tokens: int = 500
    ) -> dict[str, Any]:
        """Ask the LLM to extract structured signals.

        Args:
            prompt: The fully assembled instruction (placeholders already
                filled by the caller, see _build_prompt and
                _build_recommendation_prompt).
            max_tokens: Completion token budget. The refined recommendation
                needs more headroom than the qualitative extraction.

        Returns:
            Parsed JSON dict, or {} on any failure (LLM is optional and
            must never break the pipeline).
        """
        if not self.enabled:
            return {}

        try:
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()

            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {})
            # Some local servers (e.g. omlx) put the answer in a separate
            # reasoning_content field; fall back to it when content is empty.
            content = message.get("content") or message.get("reasoning_content") or ""

            result = _extract_json_object(content)
            if not result:
                raise LLMError("empty or unparseable JSON response")
            return result

        except LLMError:
            raise
        except Exception as exc:
            # LLM is optional, but a failure is meaningful: re-raise so the
            # caller can warn the user instead of silently degrading.
            raise LLMError(str(exc)) from exc


async def analyze_qualitative_with_llm(
    repo, config: LLMConfig, warnings: list[str] | None = None
) -> QualitativeSignals:
    """Use the LLM to extract qualitative facts from repository text.

    Scope is limited to signals with NO deterministic implementation:
    roadmap, security policy content, commercial support, and the
    project's self-declared maintenance state. Returns empty signals when
    the LLM is disabled, has no text to analyze, or fails. On failure, a
    localized warning is appended to ``warnings``.
    """
    if not config.enabled:
        return QualitativeSignals()

    texts = []
    if repo.readme_content:
        texts.append(f"README:\n{repo.readme_content[:2000]}")  # Limit size
    if repo.governance_content:
        texts.append(f"GOVERNANCE:\n{repo.governance_content[:1500]}")
    if repo.security_content:
        texts.append(f"SECURITY:\n{repo.security_content[:1000]}")

    if not texts:
        return QualitativeSignals()

    combined_text = "\n\n".join(texts)

    provider = LLMProvider(config)
    try:
        prompt = _build_prompt("sustainability and governance").replace(
            "{text}", combined_text
        )
        raw = await provider.extract_signals(prompt)
        return _parse_qualitative(raw)
    except LLMError:
        _append_warning(warnings, "warn_llm_unavailable")
        return QualitativeSignals()
    finally:
        await provider.close()


async def analyze_recommendation_with_llm(
    result, config: LLMConfig, warnings: list[str] | None = None
) -> LLMRecommendation | None:
    """Use the LLM to produce a refined recommendation from the full report.

    The LLM receives a compact digest of every indicator family plus the
    deterministic verdict, and returns a nuanced recommendation (level,
    message, explanation, confidence). Returns None when the LLM is
    disabled or fails; the deterministic verdict always stands. On
    failure, a localized warning is appended to ``warnings``.
    """
    if not config.enabled:
        return None

    provider = LLMProvider(config)
    try:
        digest = json.dumps(
            _build_report_digest(result), default=str, ensure_ascii=False
        )
        prompt = _build_recommendation_prompt().replace("{digest}", digest)
        raw = await provider.extract_signals(prompt, max_tokens=1500)
        return _parse_recommendation(raw)
    except LLMError:
        _append_warning(warnings, "warn_llm_unavailable")
        return None
    finally:
        await provider.close()


def _append_warning(warnings: list[str] | None, key: str) -> None:
    """Append a localized warning message to the list, if provided."""
    if warnings is not None:
        from gh_score.i18n import t

        warnings.append(t(key))
