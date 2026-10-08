"""AI-authorship detection ("vibe coding" signal).

AI coding agents are neither humans nor automation bots: a commit can be
*written* by an agent, or *co-authored* by one on top of a human commit
(Claude Code, Copilot, Cursor, Codex, Devin, Aider…).

Detection relies on **self-declared evidence** — commit trailers and author
identities — rather than on a bare login, which can collide with a human
name. The strongest signal is the co-author trailer: it catches an agent
that assisted a human-authored commit, which GitHub's ``/contributors``
endpoint never lists.
"""

from __future__ import annotations

import re

# Commit trailers and markers left by coding agents. Each entry maps a
# display name to a case-insensitive pattern.
_COMMIT_AGENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Claude", re.compile(r"co-authored-by:\s*claude\b", re.IGNORECASE)),
    ("Claude", re.compile(r"claude-session:\s*\S", re.IGNORECASE)),
    ("Claude Code", re.compile(r"generated with \[claude code\]", re.IGNORECASE)),
    ("Copilot", re.compile(r"co-authored-by:\s*copilot\b", re.IGNORECASE)),
    ("Cursor", re.compile(r"co-authored-by:\s*cursor", re.IGNORECASE)),
    ("Codex", re.compile(r"co-authored-by:\s*(?:openai\s+)?codex\b", re.IGNORECASE)),
    ("Devin", re.compile(r"devin-ai-integration", re.IGNORECASE)),
    ("Aider", re.compile(r"co-authored-by:\s*aider\b", re.IGNORECASE)),
    ("OpenHands", re.compile(r"co-authored-by:\s*openhands", re.IGNORECASE)),
    ("OpenHands", re.compile(r"openhands-agent", re.IGNORECASE)),
)

# Author logins reserved by coding-agent integrations. A bare login is weak
# evidence on its own, but these names are not ordinary human handles.
_AI_LOGIN_AGENTS: dict[str, str] = {
    "claude": "Claude",
    "copilot": "Copilot",
    "copilot-swe-agent": "Copilot",
    "cursoragent": "Cursor",
    "cursor-agent": "Cursor",
    "codegen-sh": "Codegen",
    "sweep-ai": "Sweep",
    "devin-ai-integration": "Devin",
    "openhands": "OpenHands",
    "openhands-agent": "OpenHands",
    "aider": "Aider",
    "gemini-code-assist": "Gemini",
}

# Email domains used by coding agents. The local part must also look like an
# agent (`noreply`, `bot`, `agent`, or the product name) so a human working
# at the company is not misclassified.
_AI_DOMAIN_AGENTS: dict[str, str] = {
    "anthropic.com": "Claude",
    "cursor.com": "Cursor",
    "githubcopilot.com": "Copilot",
    "cognition.ai": "Devin",
    "codegen.com": "Codegen",
}
_AI_LOCALPARTS = frozenset({"noreply", "no-reply", "bot", "agent"})


def _strip_bot_suffix(login: str) -> str:
    """Drop the ``[bot]`` suffix GitHub appends to app accounts."""
    return login[:-5] if login.endswith("[bot]") else login


def agent_for_login(login: str | None) -> str | None:
    """Agent name when a GitHub login is a known coding-agent account."""
    if not login:
        return None
    return _AI_LOGIN_AGENTS.get(_strip_bot_suffix(login).lower())


def agent_for_email(email: str | None) -> str | None:
    """Agent name when an author email is a known coding-agent address.

    Requires both a known service domain and an agent-looking local part, so
    a human employee is not mistaken for the agent.
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.rpartition("@")
    agent = _AI_DOMAIN_AGENTS.get(domain.lower())
    if not agent:
        return None
    local_lower = local.lower()
    if (
        local_lower in _AI_LOCALPARTS
        or "agent" in local_lower
        or "bot" in local_lower
        or "claude" in local_lower
        or "copilot" in local_lower
    ):
        return agent
    return None


def agent_for_domain(domain: str | None) -> str | None:
    """Agent name for an email domain alone (local mode fallback)."""
    if not domain:
        return None
    return _AI_DOMAIN_AGENTS.get(domain.lower())


def commit_ai_agents(message: str) -> set[str]:
    """Display names of the agents that signed a commit message."""
    return {name for name, pattern in _COMMIT_AGENT_PATTERNS if pattern.search(message)}
