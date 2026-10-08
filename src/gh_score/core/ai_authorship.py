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
# display name to a case-insensitive pattern. Co-author / session trailers
# are anchored at the start of a line (`re.MULTILINE`): a commit message
# that merely *quotes* the pattern in prose must not count.
_M = re.MULTILINE
_COMMIT_AGENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Claude", re.compile(r"^co-authored-by:\s*claude\b", re.IGNORECASE | _M)),
    ("Claude", re.compile(r"^claude-session:\s*\S", re.IGNORECASE | _M)),
    (
        "Claude Code",
        re.compile(
            r"^[^\w\n]{0,3}generated with \[claude code\]\(",
            re.IGNORECASE | _M,
        ),
    ),
    ("Copilot", re.compile(r"^co-authored-by:\s*copilot\b", re.IGNORECASE | _M)),
    ("Cursor", re.compile(r"^co-authored-by:\s*cursor", re.IGNORECASE | _M)),
    (
        "Codex",
        re.compile(r"^co-authored-by:\s*(?:openai\s+)?codex\b", re.IGNORECASE | _M),
    ),
    (
        "Devin",
        re.compile(r"^co-authored-by:.*devin-ai-integration", re.IGNORECASE | _M),
    ),
    ("Aider", re.compile(r"^co-authored-by:\s*aider\b", re.IGNORECASE | _M)),
    ("OpenHands", re.compile(r"^co-authored-by:\s*openhands", re.IGNORECASE | _M)),
    ("OpenHands", re.compile(r"openhands-agent", re.IGNORECASE)),
)

# Author logins reserved by coding-agent integrations. A bare login is weak
# evidence on its own, but these names are not ordinary human handles.
# Local git author *names* can carry a model suffix ("OpenCode gpt-5"),
# hence the prefix match in `agent_for_login`.
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
    "opencode": "OpenCode",
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

# Local parts that identify an agent on any domain — coding agents without a
# dedicated email domain (e.g. OpenCode commits as `opencode@…`).
_AI_LOCALPART_AGENTS: dict[str, str] = {
    "opencode": "OpenCode",
}


def _strip_bot_suffix(login: str) -> str:
    """Drop the ``[bot]`` suffix GitHub appends to app accounts."""
    return login[:-5] if login.endswith("[bot]") else login


def agent_for_login(login: str | None) -> str | None:
    """Agent name when a GitHub login (or git author name) is an agent.

    Matches the reserved token exactly, or as a prefix followed by a space:
    local git author names carry a model suffix ("OpenCode deepseek-v4-flash",
    "Claude Code"), while GitHub logins have no spaces.
    """
    if not login:
        return None
    normalized = _strip_bot_suffix(login).lower().strip()
    exact = _AI_LOGIN_AGENTS.get(normalized)
    if exact:
        return exact
    for token, agent in _AI_LOGIN_AGENTS.items():
        if normalized.startswith(token + " "):
            return agent
    return None


def agent_for_email(email: str | None) -> str | None:
    """Agent name when an author email is a known coding-agent address.

    Requires both a known service domain and an agent-looking local part, so
    a human employee is not mistaken for the agent. A few local parts
    identify an agent on any domain (`opencode@…`).
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.rpartition("@")
    local_lower = local.lower()
    by_localpart = _AI_LOCALPART_AGENTS.get(local_lower)
    if by_localpart:
        return by_localpart
    agent = _AI_DOMAIN_AGENTS.get(domain.lower())
    if not agent:
        return None
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
