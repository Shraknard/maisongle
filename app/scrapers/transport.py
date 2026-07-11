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


class Transport(Protocol):
    """Fetch through an anti-bot front, transport-agnostically.

    ``post_json`` POSTs a JSON body and parses the JSON reply (Leboncoin's API);
    ``get_text`` GETs a URL and returns the raw response body (SeLoger's HTML).
    Both return None on failure so a scraper can tell a block from empty results.
    """
    async def post_json(self, url: str, body: dict, headers: dict) -> Optional[dict]:
        ...

    async def get_text(
        self, url: str, headers: dict, render_js: Optional[bool] = None
    ) -> Optional[str]:
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

    async def get_text(
        self, url: str, headers: dict, render_js: Optional[bool] = None
    ) -> Optional[str]:
        # render_js is ignored: curl_cffi cannot execute JS. SeLoger's HTML is
        # server-rendered, so the initialData blob is present without rendering.
        from curl_cffi.requests import AsyncSession  # lazy: heavy import

        merged = {
            **headers,
            "User-Agent": self.user_agent,
            "Cookie": f"datadome={self.datadome}",
        }
        async with AsyncSession() as session:
            resp = await session.get(
                url, headers=merged, impersonate=self.impersonate, timeout=self.timeout,
            )
        if resp.status_code != 200:
            logger.warning("transport(cookie): HTTP %s sur %s", resp.status_code, url)
            return None
        return resp.text

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
                 render_js: bool = False, max_credits: int = 0):
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
        self.total_cost = 0  # Scrapfly credits billed over this transport's life
        # Hard budget guard: once this many credits are spent, further requests
        # return None (a source builds a fresh transport per run, so it caps
        # per-source-per-run). 0 disables the cap.
        self.max_credits = max_credits

    def _config(self, url: str, method: str, *, body: Optional[str] = None,
                headers: Optional[dict] = None, render_js: Optional[bool] = None):
        extra = {"body": body} if body is not None else {}
        return self._ScrapeConfig(
            url=url,
            method=method,
            headers=headers or {},
            asp=True,                       # bypass DataDome / anti-bot
            country=self.country,           # residential FR exit (FR-only sites)
            proxy_pool=self.proxy_pool,
            # render_js doubles the credit cost; callers opt in per request.
            render_js=self.render_js if render_js is None else render_js,
            raise_on_upstream_error=False,  # a 403 is data, not an exception
            retry=False,                    # we handle retries ourselves
            **extra,
        )

    async def _scrape(self, config, url: str):
        """Run a scrape, bill its credits, and return the raw content (or None)."""
        if self.max_credits and self.total_cost >= self.max_credits:
            logger.warning(
                "transport(scrapfly): plafond de %d crédits atteint (%d) — stop",
                self.max_credits, self.total_cost,
            )
            return None
        try:
            res = await self._client.async_scrape(config)
        except Exception as exc:  # noqa: BLE001 — Scrapfly raises on quota/credit/network
            logger.error("transport(scrapfly): échec sur %s: %s", url, exc)
            return None

        # A returned response is billed even when DataDome stealth-blocks (200 +
        # empty); failed scrapes raise above and are not billed.
        cost = getattr(res, "cost", None) or 0
        self.total_cost += cost
        logger.debug("transport(scrapfly): %s crédits (cumul %s)", cost, self.total_cost)

        status = getattr(res, "upstream_status_code", None)
        content = (res.scrape_result or {}).get("content") if res.scrape_result else None
        if status and status != 200:
            logger.warning("transport(scrapfly): upstream HTTP %s sur %s", status, url)
            return None
        if not content:
            logger.warning("transport(scrapfly): réponse vide sur %s", url)
            return None
        return content

    async def post_json(self, url: str, body: dict, headers: dict) -> Optional[dict]:
        config = self._config(
            url, "POST",
            body=json.dumps(body, separators=(",", ":")),
            headers={**headers, "Content-Type": "application/json"},
        )
        content = await self._scrape(config, url)
        if content is None:
            return None
        if isinstance(content, dict):
            return content
        if isinstance(content, list):
            return None
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            logger.warning("transport(scrapfly): contenu non-JSON sur %s", url)
            return None
        return data if isinstance(data, dict) else None

    async def get_text(
        self, url: str, headers: dict, render_js: Optional[bool] = None
    ) -> Optional[str]:
        config = self._config(url, "GET", headers=headers, render_js=render_js)
        content = await self._scrape(config, url)
        return content if isinstance(content, str) else None

    async def aclose(self) -> None:
        if self.total_cost:
            logger.info("transport(scrapfly): %d crédits utilisés sur ce run", self.total_cost)
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
