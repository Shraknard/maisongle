"""Pluggable HTTP transports for anti-bot-protected JSON APIs.

A scraper builds its request (URL, JSON body, app headers) and hands it to a
transport, which decides *how* the request actually reaches the target:

- ``DirectCookieTransport`` — curl_cffi with a TLS-impersonated fingerprint and a
  manually supplied ``datadome`` cookie. Cheap, but the cookie is IP-bound and
  must be refreshed by hand; mostly a fallback.
- ``ScrapflyTransport`` — routes through Scrapfly's Web Unlocker (``asp=true``),
  which fronts the request with rotating residential IPs and solves DataDome on
  its side. Fully automated and the home IP is never exposed.

Both expose the same ``post_json`` coroutine, so a scraper is transport-agnostic.
The choice is made per source from settings (see ``leboncoin._transport``).
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Protocol

logger = logging.getLogger("scrapers.transport")


class JsonTransport(Protocol):
    """POST a JSON body and return the parsed JSON response, or None on failure."""
    async def post_json(self, url: str, body: dict, headers: dict) -> Optional[dict]:
        ...

    async def aclose(self) -> None:
        ...


def _looks_blocked(data) -> bool:
    """DataDome answers a blocked request with a captcha-delivery redirect URL."""
    return isinstance(data, dict) and "captcha" in (data.get("url") or "")


class DirectCookieTransport:
    """curl_cffi POST with a TLS-impersonated fingerprint and an injected cookie.

    The ``datadome`` cookie must come from a real browser session (it cannot be
    minted by an HTTP client) and is tied to the IP it was issued from — so this
    only works when the app runs on the same residential IP as that browser.
    """

    def __init__(self, datadome: str, user_agent: str,
                 impersonate: str = "chrome", timeout: float = 30.0):
        self.datadome = datadome
        self.user_agent = user_agent
        self.impersonate = impersonate
        self.timeout = timeout

    async def post_json(self, url: str, body: dict, headers: dict) -> Optional[dict]:
        from curl_cffi.requests import AsyncSession  # lazy: heavy import

        merged = {
            **headers,
            "User-Agent": self.user_agent,
            "Cookie": f"datadome={self.datadome}",
        }
        async with AsyncSession() as session:
            resp = await session.post(
                url, json=body, headers=merged,
                impersonate=self.impersonate, timeout=self.timeout,
            )
        if resp.status_code != 200:
            logger.warning("transport(cookie): HTTP %s sur %s", resp.status_code, url)
            return None
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError):
            logger.warning("transport(cookie): réponse non-JSON sur %s", url)
            return None
        if _looks_blocked(data):
            logger.warning("transport(cookie): cookie datadome rejeté (captcha) sur %s", url)
            return None
        return data

    async def aclose(self) -> None:
        return None


class ScrapflyTransport:
    """Route the request through Scrapfly's Web Unlocker (residential + ASP).

    Scrapfly supplies the proxy IP and clears DataDome, so no cookie, browser or
    proxy is managed here. ``async_scrape`` is a plain coroutine that owns its own
    session, so the client needs no context management.
    """

    def __init__(self, api_key: str, country: str = "fr",
                 proxy_pool: str = "public_residential_pool",
                 render_js: bool = False, timeout: int = 90):
        try:
            from scrapfly import ScrapflyClient, ScrapeConfig
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "scrapfly-sdk n'est pas installé (pip install scrapfly-sdk)"
            ) from exc
        self._ScrapeConfig = ScrapeConfig
        self._client = ScrapflyClient(key=api_key)
        self.country = country
        self.proxy_pool = proxy_pool
        self.render_js = render_js
        self.timeout = timeout

    async def post_json(self, url: str, body: dict, headers: dict) -> Optional[dict]:
        config = self._ScrapeConfig(
            url=url,
            method="POST",
            body=json.dumps(body, separators=(",", ":")),
            headers={**headers, "Content-Type": "application/json"},
            asp=True,                       # bypass DataDome / anti-bot
            country=self.country,           # residential FR exit (Leboncoin is FR-only)
            proxy_pool=self.proxy_pool,
            render_js=self.render_js,       # JSON API: no browser rendering needed
            raise_on_upstream_error=False,  # a 403 is data, not an exception
            timeout=self.timeout,
        )
        try:
            res = await self._client.async_scrape(config)
        except Exception as exc:  # noqa: BLE001 — Scrapfly raises on quota/credit/network
            logger.error("transport(scrapfly): échec sur %s: %s", url, exc)
            return None

        status = getattr(res, "upstream_status_code", None)
        content = (res.scrape_result or {}).get("content") if res.scrape_result else None
        if status and status != 200:
            logger.warning("transport(scrapfly): upstream HTTP %s sur %s", status, url)
            return None
        if not content:
            logger.warning("transport(scrapfly): réponse vide sur %s", url)
            return None
        if isinstance(content, (dict, list)):
            return content if isinstance(content, dict) else None
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            logger.warning("transport(scrapfly): contenu non-JSON sur %s", url)
            return None
        return data if isinstance(data, dict) else None

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
