"""Tests for the website availability analyzer."""

from __future__ import annotations

from gh_score.core.analyzers.website import analyze_website
from gh_score.core.models import Status, WebsiteError, WebsiteInfo


def _info(
    url: str = "https://example.com",
    status_code: int | None = None,
    error: WebsiteError | None = None,
    captcha: bool = False,
    captcha_type: str | None = None,
) -> WebsiteInfo:
    return WebsiteInfo(
        url=url,
        status_code=status_code,
        error=error,
        captcha=captcha,
        captcha_type=captcha_type,
    )


class TestAnalyzeWebsite:
    def test_no_homepage(self):
        ind = analyze_website(None)
        assert ind.status == Status.UNKNOWN
        assert ind.interpretation

    def test_ok(self):
        ind = analyze_website(_info(status_code=200))
        assert ind.status == Status.HEALTHY
        assert ind.url == "https://example.com"
        assert ind.interpretation

    def test_dns(self):
        ind = analyze_website(_info(error=WebsiteError.DNS))
        assert ind.status == Status.CRITICAL
        assert ind.error == "dns"

    def test_timeout(self):
        ind = analyze_website(_info(error=WebsiteError.TIMEOUT))
        assert ind.status == Status.WARNING

    def test_http_error(self):
        ind = analyze_website(_info(error=WebsiteError.HTTP, status_code=500))
        assert ind.status == Status.CRITICAL

    def test_redirect_loop(self):
        ind = analyze_website(_info(error=WebsiteError.REDIRECT))
        assert ind.status == Status.CRITICAL

    def test_captcha(self):
        ind = analyze_website(_info(status_code=403, captcha=True, captcha_type="cloudflare"))
        assert ind.status == Status.WARNING
        assert ind.captcha is True
        assert ind.captcha_type == "cloudflare"

    def test_other_error(self):
        ind = analyze_website(_info(error=WebsiteError.OTHER))
        assert ind.status == Status.WARNING
