"""LLM provider for optional qualitative analysis.

Supports Ollama (default) and OpenAI-compatible APIs.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from gh_score.config import LLMConfig


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

    async def extract_signals(self, text: str, context: str) -> dict[str, Any]:
        """Extract structured signals from text using LLM.

        Args:
            text: Text to analyze (README, GOVERNANCE, etc.)
            context: What to look for (e.g., "sustainability", "governance")

        Returns:
            Dict with extracted signals, or empty dict on failure
        """
        if not self.enabled:
            return {}

        prompt = f"""Analyze the following text and extract information about {context}.

Return a JSON object with these fields (only include fields you can confidently extract):
- sponsors: list of mentioned sponsors or backing companies
- governance_model: governance model (BDFL, core team, foundation, corporate-owned, etc.)
- roadmap: any mentioned roadmap or future plans
- security_policy: any security policy mentions
- commercial_support: any commercial support mentions
- maintenance_status: any concerning language about maintenance

Text:
{text}

Return only valid JSON, no markdown formatting."""

        try:
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            response.raise_for_status()

            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Parse JSON from response
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


async def analyze_sustainability_with_llm(
    repo, config: LLMConfig
) -> dict[str, Any]:
    """Use LLM to extract sustainability signals from repository text.

    Combines README + GOVERNANCE + SECURITY content and asks LLM to extract:
    - Sponsors, backers, supporting companies
    - Governance model
    - Roadmap, security policy, commercial support mentions
    - Maintenance status concerns

    Args:
        repo: Repository model with text content
        config: LLM configuration

    Returns:
        Dict with extracted signals, or empty dict if LLM disabled or failed
    """
    if not config.enabled:
        return {}

    # Combine available text content
    texts = []
    if repo.readme_content:
        texts.append(f"README:\n{repo.readme_content[:2000]}")  # Limit size
    if repo.governance_content:
        texts.append(f"GOVERNANCE:\n{repo.governance_content[:1500]}")
    if repo.security_content:
        texts.append(f"SECURITY:\n{repo.security_content[:1000]}")

    if not texts:
        return {}

    combined_text = "\n\n".join(texts)

    provider = LLMProvider(config)
    try:
        return await provider.extract_signals(combined_text, "sustainability and governance")
    finally:
        await provider.close()
