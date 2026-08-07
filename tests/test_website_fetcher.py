"""Tests for the website availability fetcher."""

from __future__ import annotations

import socket

import httpx
import pytest

from gh_score.core.cache import Cache
from gh_score.core.fetchers.website import _detect_captcha, probe_website
from gh_score.core.models import WebsiteError


def _resp(status: int, text: str = "", headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, text=text, headers=headers or {})


class TestCaptchaDetection:
    def test_plain_page(self):
        assert _detect_captcha(httpx.Headers({}), b"<html>Welcome</html>") == (False, None)

    def test_recaptcha_html(self):
        assert _detect_captcha(httpx.Headers({}), b'<div class="g-recaptcha"></div>') == (True, "recaptcha")

    def test_cloudflare_challenge_header(self):
        assert _detect_captcha(httpx.Headers({"cf-mitigated": "challenge"}), b"") == (True, "cloudflare")

    def test_hcaptcha_title(self):
        assert _detect_captcha(httpx.Headers({}), b"<title>Please verify you are human</title>") == (True, "generic")


def _raise_network_error(req: httpx.Request) -> httpx.Response:
    raise AssertionError("network must not be called")


class TestProbeWebsite:
    @pytest.mark.asyncio
    async def test_ok(self):
        transport = httpx.MockTransport(lambda req: _resp(200, "<html>hi</html>"))
        info = await probe_website("https://example.com", transport=transport)
        assert info.status_code == 200
        assert info.error is None
        assert info.captcha is False
        assert info.final_url == "https://example.com"

    @pytest.mark.asyncio
    async def test_redirect_followed(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/start":
                return httpx.Response(302, headers={"location": "/final"})
            return _resp(200, "final page")

        info = await probe_website("https://example.com/start", transport=httpx.MockTransport(handler))
        assert info.status_code == 200
        assert info.final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_redirect_loop(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": str(req.url)})

        info = await probe_website("https://example.com/loop", transport=httpx.MockTransport(handler))
        assert info.error == WebsiteError.REDIRECT

    @pytest.mark.asyncio
    async def test_dns_failure(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("name resolution failed", request=req) from socket.gaierror(
                socket.EAI_NONAME, "Name or service not known"
            )

        info = await probe_website("https://no-such-host.invalid", transport=httpx.MockTransport(handler))
        assert info.error == WebsiteError.DNS

    @pytest.mark.asyncio
    async def test_timeout(self):
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=req)

        info = await probe_website("https://slow.example.com", transport=httpx.MockTransport(handler))
        assert info.error == WebsiteError.TIMEOUT

    @pytest.mark.asyncio
    async def test_http_500(self):
        info = await probe_website("https://example.com", transport=httpx.MockTransport(lambda req: _resp(500)))
        assert info.error == WebsiteError.HTTP
        assert info.status_code == 500

    @pytest.mark.asyncio
    async def test_captcha_page(self):
        transport = httpx.MockTransport(
            lambda req: _resp(403, "<html><title>Please verify you are human</title></html>")
        )
        info = await probe_website("https://example.com", transport=transport)
        assert info.captcha is True
        assert info.captcha_type == "generic"
        assert info.status_code == 403

    @pytest.mark.asyncio
    async def test_cache_hit_skips_network(self, tmp_path):
        cache = Cache(str(tmp_path))
        ok_transport = httpx.MockTransport(lambda req: _resp(200, "cached body"))
        await probe_website("https://example.com", cache=cache, transport=ok_transport)

        info = await probe_website(
            "https://example.com", cache=cache, transport=httpx.MockTransport(_raise_network_error)
        )
        assert info.status_code == 200
