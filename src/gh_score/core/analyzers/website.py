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
