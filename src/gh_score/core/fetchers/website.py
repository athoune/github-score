"""Website availability fetcher.

Probes a project homepage: DNS resolution, timeout, HTTP status (redirects
followed) and bot-protection ("I'm not a robot") detection.
"""

from __future__ import annotations

import socket
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import httpx

from gh_score.core.cache import Cache
from gh_score.core.models import WebsiteError, WebsiteInfo

# Per-phase timeouts so a stalled site does not hang the whole pipeline.
_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0)

_MAX_REDIRECTS = 10

# How much of the response body to sample for captcha detection.
_CAPTCHA_SAMPLE_BYTES = 64 * 1024

# (marker, kind) pairs, matched case-insensitively on a joined sample of
# response headers + title + first bytes of HTML.
_CAPTCHA_MARKERS: tuple[tuple[str, str], ...] = (
    ("recaptcha", "recaptcha"),
    ("g-recaptcha", "recaptcha"),
    ("hcaptcha", "hcaptcha"),
    ("turnstile", "turnstile"),
    ("challenge-platform", "cloudflare"),
    ("imnotarobot", "generic"),
    ("not a robot", "generic"),
    ("verify you are human", "generic"),
    ("are you a human", "generic"),
    ("captcha", "generic"),
)

_USER_AGENT = "gh-score/0.1.0"


def _detect_captcha(
    headers: httpx.Headers,
    html_sample: bytes,
) -> tuple[bool, str | None]:
    """Keyword heuristic over response headers and the first page bytes."""
    # A Cloudflare "cf-mitigated: challenge" header is a strong, explicit
    # signal; header names are not part of the body sample below.
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True, "cloudflare"
    sample = " ".join(
        [
            headers.get("server", ""),
            html_sample.decode("utf-8", errors="replace").lower(),
        ]
    )
    for marker, kind in _CAPTCHA_MARKERS:
        if marker in sample:
            return True, kind
    return False, None


def _classify_request_error(exc: httpx.RequestError) -> tuple[WebsiteError, str]:
    """Map an httpx request exception to our error taxonomy."""
    if isinstance(exc, httpx.TimeoutException):
        return WebsiteError.TIMEOUT, exc.__class__.__name__
    if isinstance(exc.__cause__, socket.gaierror):
        return WebsiteError.DNS, exc.__cause__.strerror or "name resolution failed"
    return WebsiteError.OTHER, str(exc)


def _to_cache_dict(info: WebsiteInfo) -> dict[str, Any]:
    d = asdict(info)
    d["error"] = info.error.value if info.error else None
    d["checked_at"] = info.checked_at.isoformat() if info.checked_at else None
    return d


def _from_cache_dict(d: dict[str, Any]) -> WebsiteInfo:
    d = dict(d)
    d["error"] = WebsiteError(d["error"]) if d.get("error") else None
    d["checked_at"] = datetime.fromisoformat(d["checked_at"]) if d.get("checked_at") else None
    return WebsiteInfo(**d)


async def probe_website(
    url: str,
    cache: Cache | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WebsiteInfo:
    """Probe a website URL and return raw availability data. Never raises.

    Follows redirects (bounded), samples the body for captcha detection,
    classifies DNS/timeout/HTTP failures, and caches the result — successes
    and failures alike, with the cache default TTL.
    """
    cache_key = f"website:{url}"
    if cache is not None:
        cached = cache.get_json(cache_key)
        if cached is not None:
            return _from_cache_dict(cached)

    info = WebsiteInfo(url=url, checked_at=datetime.now(timezone.utc))
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            headers={"User-Agent": _USER_AGENT},
            transport=transport,
        ) as client:
            async with client.stream("GET", url) as resp:
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _CAPTCHA_SAMPLE_BYTES:
                        break
                info.status_code = resp.status_code
                info.final_url = str(resp.url)
                if resp.status_code >= 400:
                    info.error = WebsiteError.HTTP
                    info.error_detail = f"HTTP {resp.status_code}"
                captcha, kind = _detect_captcha(resp.headers, b"".join(chunks))
                info.captcha = captcha
                info.captcha_type = kind
    except httpx.TooManyRedirects as exc:
        info.error, info.error_detail = WebsiteError.REDIRECT, str(exc)
    except httpx.TimeoutException as exc:
        info.error, info.error_detail = WebsiteError.TIMEOUT, exc.__class__.__name__
    except httpx.RequestError as exc:
        info.error, info.error_detail = _classify_request_error(exc)
    except Exception as exc:  # noqa: BLE001 — the probe must never crash the pipeline
        info.error, info.error_detail = WebsiteError.OTHER, str(exc)

    if cache is not None:
        cache.set_json(cache_key, _to_cache_dict(info))

    return info
