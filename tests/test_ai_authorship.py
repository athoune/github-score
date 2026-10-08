"""Tests for AI-authorship detection (pure helpers)."""

from __future__ import annotations

from gh_score.core.ai_authorship import (
    agent_for_domain,
    agent_for_email,
    agent_for_login,
    commit_ai_agents,
)


class TestAgentForLogin:
    def test_claude(self):
        assert agent_for_login("claude") == "Claude"

    def test_copilot_bot_suffix(self):
        assert agent_for_login("copilot-swe-agent[bot]") == "Copilot"

    def test_cursor_agent(self):
        assert agent_for_login("cursoragent") == "Cursor"

    def test_human_login(self):
        assert agent_for_login("octocat") is None
        assert agent_for_login(None) is None


class TestAgentForEmail:
    def test_claude_noreply(self):
        assert agent_for_email("noreply@anthropic.com") == "Claude"

    def test_copilot(self):
        assert agent_for_email("bot@githubcopilot.com") == "Copilot"

    def test_human_at_agent_company_not_flagged(self):
        # A human employee must not be mistaken for the agent.
        assert agent_for_email("jane.doe@anthropic.com") is None

    def test_unknown_domain(self):
        assert agent_for_email("alice@example.com") is None

    def test_no_email(self):
        assert agent_for_email(None) is None
        assert agent_for_email("not-an-email") is None


class TestAgentForDomain:
    def test_known(self):
        assert agent_for_domain("anthropic.com") == "Claude"

    def test_unknown(self):
        assert agent_for_domain("example.com") is None


class TestCommitAiAgents:
    def test_claude_trailer(self):
        message = (
            "engine: fix params\n\n"
            "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_abc"
        )
        assert commit_ai_agents(message) == {"Claude"}

    def test_copilot_trailer(self):
        message = "Fix build\n\nCo-authored-by: Copilot <copilot@github.com>"
        assert "Copilot" in commit_ai_agents(message)

    def test_devin(self):
        assert commit_ai_agents("Co-authored-by: devin-ai-integration[bot]") == {
            "Devin"
        }

    def test_human_message(self):
        assert commit_ai_agents("Fix the parser\n\nReviewed-by: Alice") == set()

    def test_plain_security_mention_is_not_ai(self):
        assert commit_ai_agents("Bump lodash for security") == set()
