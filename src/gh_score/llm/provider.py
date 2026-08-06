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
from gh_score.core.models import QualitativeSignals


# Allowed values for the self-declared maintenance state the LLM may report.
_TEXT_MAINTENANCE_STATES = frozenset({"active", "maintenance", "abandoned", "unknown"})

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

    async def extract_signals(self, prompt: str, text: str) -> dict[str, Any]:
        """Ask the LLM to extract structured signals.

        Args:
            prompt: The full instruction (see _build_prompt).
            text: The repository text excerpts to analyze.

        Returns:
            Parsed JSON dict, or {} on any failure (LLM is optional and
            must never break the pipeline).
        """
        if not self.enabled:
            return {}

        filled = prompt.replace("{text}", text)

        try:
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "user", "content": filled},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            response.raise_for_status()

            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Try to extract JSON from markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content.strip())
            return result if isinstance(result, dict) else {}

        except Exception:
            # LLM is optional, never break the pipeline
            return {}


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
        prompt = _build_prompt("sustainability and governance")
        raw = await provider.extract_signals(prompt, combined_text)
        return _parse_qualitative(raw)
    except Exception:
        return QualitativeSignals()
    finally:
        await provider.close()
