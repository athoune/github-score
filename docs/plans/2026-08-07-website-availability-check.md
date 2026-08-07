# Website Availability Check — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Probe the project homepage (DNS resolution, timeout, HTTP status with redirects followed, bot-protection detection) and feed the result into the traffic-light verdict.

**Architecture:** Follows the existing fetcher → analyzer pattern. `fetchers/website.py` performs the HTTP probe (async httpx, injected transport for tests, filesystem cache), `analyzers/website.py` maps the raw probe to a `Status` + localized interpretation, and `recommendation.py` gains a `_rec_website` branch (site down → red, site degraded → orange) inserted right after the hard red flags. The result is displayed in TUI, Markdown and JSON (JSON gets it for free via `asdict`).

**Decisions taken with the maintainer:**
- The availability check **feeds the recommendation** (RULES.md + SPECS.md + i18n updated in the same change).
- Probe **`meta.homepage` only**; repos without a homepage get `UNKNOWN` and skip the verdict branches.
- Failure mapping: **DNS → CRITICAL**, **timeout → WARNING**, **HTTP 4xx/5xx → CRITICAL**, **redirect loop → CRITICAL**, **200 + captcha → WARNING**, **2xx → HEALTHY**, anything else → WARNING.
- Captcha = **keyword heuristic** on headers + title + first 64 KiB of HTML (recaptcha, hcaptcha, Cloudflare challenge/turnstile, "not a robot", …).

**Tech Stack:** Python 3.12, httpx (`AsyncClient` + `MockTransport` for tests), asyncio, rich (TUI), pytest, existing `Cache` (filesystem, TTL).

**Non-goals:** No HEAD requests, no rendering, no page-content analysis beyond the captcha heuristic, no change to `_compute_confidence` (the website branch only downgrades, never upgrades, so it stays out of confidence accounting like registries/license/languages).

---

### Task 1: Data models

**Files:**
- Modify: `src/gh_score/core/models.py` (add after `RegistryInfo`, after `SustainabilityIndicator`, and the two aggregate fields)

**Step 1: Write the failing test** — no test in isolation; models are exercised by every later task. Skipped (models are trivial dataclasses covered by Task 2+ tests).

**Step 2: Implement**

```python
# ---------------------------------------------------------------------------
# Website availability
# ---------------------------------------------------------------------------

class WebsiteError(Enum):
    """Why a homepage probe failed."""
    DNS = "dns"            # domain name resolution failed
    TIMEOUT = "timeout"    # connect/read timed out
    HTTP = "http"          # server answered a non-2xx status
    REDIRECT = "redirect"  # redirect loop
    OTHER = "other"        # any other failure


@dataclass
class WebsiteInfo:
    """Raw homepage probe result."""
    url: str
    status_code: int | None = None
    final_url: str | None = None
    error: WebsiteError | None = None
    error_detail: str | None = None
    captcha: bool = False
    captcha_type: str | None = None  # "recaptcha" | "hcaptcha" | "cloudflare" | "turnstile" | "generic"
    checked_at: datetime | None = None


@dataclass
class WebsiteIndicator:
    """Analyzed homepage availability (shown in the report)."""
    url: str | None = None
    status_code: int | None = None
    final_url: str | None = None
    error: str | None = None          # WebsiteError.value, for JSON serialization
    error_detail: str | None = None
    captcha: bool = False
    captcha_type: str | None = None
    status: Status = Status.UNKNOWN
    interpretation: str = ""
```

- `Repository`: add field `website_info: WebsiteInfo | None = None` (raw aggregate, like `registries`).
- `AnalysisResult`: add field `website: WebsiteIndicator = field(default_factory=WebsiteIndicator)` (has a default so no existing construction site breaks).

**Step 3: Verify** — `uv run pytest tests/ -q` still green.

**Step 4: Commit**
```bash
git commit -m "feat: add website availability models"
```

---

### Task 2: Website fetcher

**Files:**
- Create: `src/gh_score/core/fetchers/website.py`
- Test: `tests/test_website_fetcher.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run to verify it fails**
`uv run pytest tests/test_website_fetcher.py -v` → FAIL with `ModuleNotFoundError: gh_score.core.fetchers.website`.

**Step 3: Implement** `src/gh_score/core/fetchers/website.py`

```python
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
    ("cf-challenge", "cloudflare"),
    ("cf-mitigated", "cloudflare"),
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
    sample = " ".join(
        [
            headers.get("server", ""),
            headers.get("cf-mitigated", ""),
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
```

**Step 4: Run to verify it passes**
`uv run pytest tests/test_website_fetcher.py -v` → PASS.

**Step 5: Commit**
```bash
git commit -m "feat: probe homepage availability (DNS, timeout, redirects, captcha)"
```

---

### Task 3: Website analyzer

**Files:**
- Create: `src/gh_score/core/analyzers/website.py`
- Test: `tests/test_website_analyzer.py`

**Step 1: Write the failing test**

```python
"""Tests for the website availability analyzer."""

from __future__ import annotations

from gh_score.core.analyzers.website import analyze_website
from gh_score.core.models import Status, WebsiteError, WebsiteInfo


def _info(**kw) -> WebsiteInfo:
    base = {"url": "https://example.com"}
    base.update(kw)
    return WebsiteInfo(**base)


class TestAnalyzeWebsite:
    def test_no_homepage(self):
        ind = analyze_website(None)
        assert ind.status == Status.UNKNOWN
        assert ind.interpretation

    def test_ok(self):
        ind = analyze_website(_info(status_code=200))
        assert ind.status == Status.HEALTHY
        assert "200" in ind.interpretation

    def test_dns(self):
        ind = analyze_website(_info(error=WebsiteError.DNS))
        assert ind.status == Status.CRITICAL

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
```

**Step 2: Run to verify it fails** → `ModuleNotFoundError`.

**Step 3: Implement** `src/gh_score/core/analyzers/website.py`

```python
"""Website availability analyzer.

Maps the raw homepage probe (``fetchers/website.py``) to a Status and a
localized interpretation.
"""

from __future__ import annotations

from gh_score.core.models import (
    Status,
    WebsiteError,
    WebsiteIndicator,
    WebsiteInfo,
)
from gh_score.i18n import t


def analyze_website(
    info: WebsiteInfo | None,
    lang: str | None = None,
) -> WebsiteIndicator:
    """Analyze homepage availability.

    Args:
        info: Raw probe result (None when the repo declares no homepage).
        lang: Language for the interpretation.

    Returns a WebsiteIndicator with status and interpretation.
    """
    indicator = WebsiteIndicator()

    if info is None:
        indicator.status = Status.UNKNOWN
        indicator.interpretation = t("int_site_no_homepage", lang=lang)
        return indicator

    indicator.url = info.url
    indicator.status_code = info.status_code
    indicator.final_url = info.final_url
    indicator.error = info.error.value if info.error else None
    indicator.error_detail = info.error_detail
    indicator.captcha = info.captcha
    indicator.captcha_type = info.captcha_type

    # Bot protection first: the site is up but we cannot read it.
    if info.captcha:
        indicator.status = Status.WARNING
        indicator.interpretation = t("int_site_captcha", lang=lang, site=info.url)
        return indicator

    if info.error == WebsiteError.DNS:
        indicator.status = Status.CRITICAL
        indicator.interpretation = t("int_site_dns", lang=lang, site=info.url)
        return indicator

    if info.error == WebsiteError.TIMEOUT:
        indicator.status = Status.WARNING
        indicator.interpretation = t("int_site_timeout", lang=lang, site=info.url)
        return indicator

    if info.error == WebsiteError.REDIRECT:
        indicator.status = Status.CRITICAL
        indicator.interpretation = t("int_site_redirect", lang=lang, site=info.url)
        return indicator

    if info.error == WebsiteError.HTTP or (
        info.status_code is not None and not 200 <= info.status_code < 300
    ):
        indicator.status = Status.CRITICAL
        indicator.interpretation = t(
            "int_site_http", lang=lang, site=info.url, code=info.status_code or 0
        )
        return indicator

    if info.error == WebsiteError.OTHER:
        indicator.status = Status.WARNING
        indicator.interpretation = t("int_site_unreachable", lang=lang, site=info.url)
        return indicator

    # Reachable (2xx)
    indicator.status = Status.HEALTHY
    indicator.interpretation = t(
        "int_site_ok", lang=lang, site=info.url, code=info.status_code or 0
    )
    return indicator
```

Also add `analyze_website` to `src/gh_score/core/analyzers/__init__.py` (import + `__all__`).

**Step 4: Run to verify it passes** — `uv run pytest tests/test_website_analyzer.py -v` → PASS.

**Step 5: Commit**
```bash
git commit -m "feat: analyze homepage availability into an indicator"
```

---

### Task 4: Recommendation integration

**Files:**
- Modify: `src/gh_score/core/analyzers/recommendation.py` (add `_rec_website`, insert into the section tuple after `_rec_red_flags`)
- Test: `tests/test_recommendation.py` (add a `TestWebsiteRecommendation` class; reuse the existing result-builder helper)

**Step 1: Write the failing test** (mirror the existing helper style; e.g. a healthy-active result plus a website variant)

```python
class TestWebsiteRecommendation:
    """A dead or degraded homepage downgrades the verdict."""

    def _rec(self, website: WebsiteIndicator) -> Recommendation:
        result = _make_result()  # existing helper: healthy active project
        result.website = website
        return analyze_recommendation(result)

    def test_site_down_is_red(self):
        ind = WebsiteIndicator(status=Status.CRITICAL, error="dns")
        assert self._rec(ind).level == RecommendationLevel.RED

    def test_captcha_is_orange(self):
        ind = WebsiteIndicator(status=Status.WARNING, captcha=True)
        assert self._rec(ind).level == RecommendationLevel.ORANGE

    def test_timeout_is_orange(self):
        ind = WebsiteIndicator(status=Status.WARNING, error="timeout")
        assert self._rec(ind).level == RecommendationLevel.ORANGE

    def test_healthy_does_not_change_verdict(self):
        ind = WebsiteIndicator(status=Status.HEALTHY, status_code=200)
        assert self._rec(ind).level == RecommendationLevel.GREEN

    def test_no_homepage_does_not_change_verdict(self):
        ind = WebsiteIndicator(status=Status.UNKNOWN)
        assert self._rec(ind).level == RecommendationLevel.GREEN
```

**Step 2: Run to verify it fails** — the healthy site still returns GREEN (no branch yet), the down/captcha cases fall through to GREEN → FAIL.

**Step 3: Implement**

```python
def _rec_website(result: AnalysisResult, lang: str) -> Recommendation | None:
    """Homepage availability: a dead site is a strong abandonment signal.

    Only fires when a homepage is declared AND the probe failed or was
    degraded; a reachable site or a missing homepage never affects the
    verdict.
    """
    site = result.website
    if site.status == Status.CRITICAL:
        reason_map = {
            "dns": ("reason_site_dns", {}),
            "http": ("reason_site_http", {"code": site.status_code or 0}),
            "redirect": ("reason_site_redirect", {}),
        }
        key, kwargs = reason_map.get(site.error, ("reason_site_down", {}))
        return _build(
            RecommendationLevel.RED,
            t("rec_site_down", lang=lang),
            result,
            lang,
            t(key, lang=lang, **kwargs),
        )
    if site.status == Status.WARNING:
        reason = "reason_site_captcha" if site.captcha else "reason_site_timeout"
        return _build(
            RecommendationLevel.ORANGE,
            t("rec_site_degraded", lang=lang),
            result,
            lang,
            t(reason, lang=lang),
        )
    return None
```

Insert `_rec_website` in the section tuple of `analyze_recommendation`, right after `_rec_red_flags` and before `_rec_ephemeral`.

**Step 4: Run to verify it passes** — `uv run pytest tests/test_recommendation.py -v` → PASS (all 28 existing + new ones).

**Step 5: Commit**
```bash
git commit -m "feat: downgrade verdict when the homepage is down or bot-protected"
```

---

### Task 5: Pipeline wiring (api.py)

**Files:**
- Modify: `src/gh_score/core/api.py`
- Test: `tests/test_api.py` (mock `probe_website` where the pipeline is exercised, so no real network)

**Step 1: Implement**

- Import `probe_website` from `gh_score.core.fetchers.website` and `analyze_website` from the analyzers group.
- After the registry fetch block, add:

```python
    # Probe the project homepage (skip when none is declared)
    if repo.meta.homepage:
        repo.website_info = await probe_website(repo.meta.homepage, cache)
```

- In the `AnalysisResult(...)` construction, add `website=analyze_website(repo.website_info),`.

**Step 2: Fix tests** — in `tests/test_api.py`, wherever `analyze_repo_async` is exercised with a fixture that declares a homepage, patch `gh_score.core.api.probe_website` to an async mock returning a `WebsiteInfo` (or a `None`-returning mock and rely on no homepage). Add one focused test asserting the pipeline stores the probed `WebsiteIndicator` on the result.

**Step 3: Verify** — `uv run pytest tests/test_api.py -v` → PASS, no network calls (assert mock called/not called as appropriate).

**Step 4: Commit**
```bash
git commit -m "feat: probe the homepage in the analysis pipeline"
```

---

### Task 6: i18n keys

**Files:**
- Modify: `src/gh_score/i18n.py` (add keys to the `fr` and `en` catalogs, in the same alphabetical neighborhood as the other `int_*`/`rec_*` keys)

French:
```python
"panel_website": "Site web",
"md_section_website": "## Site web",
"int_site_no_homepage": "Pas de page d'accueil déclarée",
"int_site_ok": "Site accessible (HTTP {code})",
"int_site_dns": "Le nom de domaine ne résout pas : {site}",
"int_site_timeout": "Le site a expiré (timeout) : {site}",
"int_site_http": "Le site répond HTTP {code} : {site}",
"int_site_redirect": "Boucle de redirection sur le site : {site}",
"int_site_captcha": "Site protégé par un contrôle anti-robot (« I'm not a robot ») : {site}",
"int_site_unreachable": "Site injoignable : {site}",
"rec_site_down": "La page d'accueil du projet est hors ligne",
"rec_site_degraded": "Page d'accueil injoignable ou protégée par un anti-robot",
"reason_site_down": "la page d'accueil est inaccessible",
"reason_site_dns": "le nom de domaine de la page d'accueil ne résout pas",
"reason_site_http": "la page d'accueil répond HTTP {code}",
"reason_site_redirect": "boucle de redirection sur la page d'accueil",
"reason_site_timeout": "la page d'accueil a expiré (timeout)",
"reason_site_captcha": "la page d'accueil est protégée par un contrôle anti-robot",
```

English equivalents (same keys):
```python
"panel_website": "Website",
"md_section_website": "## Website",
"int_site_no_homepage": "No homepage declared",
"int_site_ok": "Site reachable (HTTP {code})",
"int_site_dns": "Domain name does not resolve: {site}",
"int_site_timeout": "Site timed out: {site}",
"int_site_http": "Site answers HTTP {code}: {site}",
"int_site_redirect": "Redirect loop on the site: {site}",
"int_site_captcha": "Site behind a bot-protection check (\"I'm not a robot\"): {site}",
"int_site_unreachable": "Site unreachable: {site}",
"rec_site_down": "Project homepage is down",
"rec_site_degraded": "Project homepage unreachable or bot-protected",
"reason_site_down": "the project homepage is unreachable",
"reason_site_dns": "the homepage domain name does not resolve",
"reason_site_http": "the homepage answers HTTP {code}",
"reason_site_redirect": "redirect loop on the homepage",
"reason_site_timeout": "the homepage timed out",
"reason_site_captcha": "the homepage is behind a bot-protection check",
```

**Verify:** `uv run pytest tests/test_i18n.py -v` → PASS (existing tests assert interpretation/rec message localization; add a small test checking `int_site_*` keys resolve in both catalogs).

**Commit:** `git commit -m "feat: localize the website availability indicator"`

---

### Task 7: RULES.md + SPECS.md (mandatory doc sync)

**Files:**
- Modify: `RULES.md`
- Modify: `SPECS.md`

**RULES.md — decision tree:** insert two steps after "1. Hard red flags" (renumber the rest):

```markdown
2. **Homepage down** (only when the repository declares a homepage; repos
   without one skip both website steps)
   - DNS resolution failure, HTTP error (4xx/5xx), or redirect loop
     → 🔴 "Project homepage is down"
   - Timeout, or page behind a bot-protection check ("I'm not a robot")
     → 🟠 "Project homepage unreachable or bot-protected"
```

**RULES.md — thresholds table:** the website probe constants live in
`fetchers/website.py`, not `recommendation.py`; add a small paragraph or
table row noting them:

| Constant | Value | Used for |
|----------|-------|----------|
| `_TIMEOUT` (website.py) | connect 10s / read 15s | homepage probe timeouts |
| `_MAX_REDIRECTS` (website.py) | 10 | redirects followed before failing |
| `_CAPTCHA_SAMPLE_BYTES` (website.py) | 64 KiB | body sample for bot-detection heuristic |

**RULES.md — message catalog table:** add the `rec_site_*` and
`reason_site_*` keys (and `int_site_*` if the table lists indicator
interpretation keys).

**SPECS.md:** add a data-source subsection (website probe: URL, DNS,
timeout, redirects, captcha heuristic, caching) and mirror the two new
decision-tree branches in its tree section.

**Verify:** re-read both files; tree order in code, RULES.md and SPECS.md
match (red flags → website → ephemeral → abandoned → active →
maintenance → text discontinued → unknown).

**Commit:** `git commit -m "docs: document the website availability rule in RULES.md and SPECS.md"`

---

### Task 8: TUI + Markdown display

**Files:**
- Modify: `src/gh_score/cli/tui.py` (add `_render_website` + layout)
- Modify: `src/gh_score/cli/main.py` (add `_md_website` + call in the report sequence)
- Test: `tests/test_tui.py`, `tests/test_cli.py`

**Step 1: Implement** — follow the existing `_render_sustainability` / `_md_sustainability` pattern exactly:

```python
# tui.py — panel showing url (when set), final_url (when different),
# captcha flag, and the interpretation; border color from the existing
# status→color mapping.
def _render_website(result: AnalysisResult) -> Panel:
    """Render the website availability panel."""
    site = result.website
    color = _STATUS_COLORS.get(site.status, "dim")
    content = Text()
    if site.url:
        content.append(f"{site.url}\n")
        if site.final_url and site.final_url != site.url:
            content.append(f"→ {site.final_url}\n")
    content.append(site.interpretation)
    return Panel(content, title=t("panel_website"), border_style=color)
```

- Insert the panel in the TUI layout after `grid2` (always rendered — even "no homepage declared" is informative).
- `main.py`: `_md_website(result, console)` printing `t("md_section_website")` then the URL (if any) and the interpretation; call it in the markdown report sequence.

**Step 2: Tests** — `test_tui.py`: assert the website panel appears with the expected title; `test_cli.py`: assert the markdown report contains the `md_section_website` heading. Fix any layout/assertion drift.

**Step 3: Verify** — `uv run pytest tests/test_tui.py tests/test_cli.py -v` → PASS.

**Step 4: Commit**
```bash
git commit -m "feat: show homepage availability in the TUI and markdown report"
```

---

### Task 9: README, TODO.md and final pass

**Files:**
- Modify: `README.md` (one line in the features list)
- Modify: `TODO.md` (bump "Last updated", refresh any drifted numbers)

**Steps:**
1. Add the availability check to the README feature list.
2. Run the full suite + prospector: `uv run pytest --cov=gh_score --cov-report=term` and `../prospector-mcp/.venv/bin/prospector .` — everything green, coverage not below ~86%.
3. Update `TODO.md` header date and any drifted coverage numbers; add a follow-up item if the website probe uncovered something (e.g. threshold tuning note in the existing "Tune the recommendation thresholds" item).
4. Commit: `git commit -m "docs: mention the homepage availability check in the README"` (plus TODO.md sync).

---

## Ordering rationale & risks

- Models → fetcher → analyzer → recommendation → pipeline → i18n → docs → UI: each task compiles and tests green on its own; no long-lived broken intermediate state.
- **Risk: probe latency** — worst case ~15 s on an uncached dead site; acceptable (single probe, cached 24 h). If it proves annoying, lower `read` timeout in a follow-up.
- **Risk: false captcha positives** — a page legitimately containing the word "captcha" (e.g. a docs page about reCAPTCHA) flags as protected. Mitigation: markers are specific (`g-recaptcha`, `cf-mitigated`, titles); accepted trade-off for a keyword heuristic.
- **Risk: redirect-following to a captive/login portal** — final URL and status are reported; a 200 login page counts as HEALTHY. Out of scope.
