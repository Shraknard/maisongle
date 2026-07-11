from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from typing import List, Optional

from app.config import get_settings
from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune
from app.scrapers.transport import Transport, DirectCookieTransport, ScrapflyTransport

logger = logging.getLogger("scrapers.logicimmo")

BASE = "https://www.logic-immo.com"

REQUEST_DELAY = 1.0
FIRST_PAGE_ATTEMPTS = 3          # ride out a DataDome stealth-block (fresh IP/retry)
RETRY_DELAY = 2.0

DISTRIBUTION = {0: "vente", 1: "location"}

# Our property type int -> Logic-Immo URL slug (one type per URL, like SeLoger).
TYPE_TO_SLUG = {
    0: "appartement", 1: "maison", 2: "parking", 3: "terrain",
    4: "local-commercial", 5: "immeuble", 6: "loft",
}
DEFAULT_TYPE_SLUGS = ["appartement", "maison"]

# Logic-Immo property-type label/enum -> our int (response side, provisional).
LI_TYPE_TO_INT = {
    "flat": 0, "apartment": 0, "appartement": 0, "loft": 6,
    "house": 1, "maison": 1, "villa": 1,
    "parking": 2, "garage": 2,
    "terrain": 3, "land": 3,
    "premises": 4, "local": 4, "shop": 4, "office": 4,
    "building": 5, "immeuble": 5,
}


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _strip_accents(name).lower()).strip("-") or "ville"


def _dept(insee: Optional[str]) -> str:
    insee = (insee or "").zfill(5)
    return insee[:3] if insee[:2] in ("97", "98") else insee[:2]


def _to_int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_next_data(html: str) -> Optional[dict]:
    """Pull a Next.js ``__NEXT_DATA__`` JSON blob out of the page (if present).

    Logic-Immo is part of the AVIV group (like SeLoger) and its rebuilt front end
    embeds its result set as server-rendered JSON. This grabs the standard Next.js
    payload; ``_listings_from`` then walks it. PROVISIONAL: the exact key path is
    unverified (DataDome blocks reconnaissance from a datacenter IP) and must be
    confirmed on the first live Scrapfly run.
    """
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S,
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        logger.warning("logicimmo: __NEXT_DATA__ illisible")
        return None
    return data if isinstance(data, dict) else None


def _listings_from(data: dict) -> List[dict]:
    """Best-effort walk to the listings array inside the embedded payload.

    PROVISIONAL: searches common container keys; validate/adjust the exact path on
    the first live run. Returns [] (source stays a safe no-op) when nothing matches.
    """
    props = (((data.get("props") or {}).get("pageProps")) or {})
    for key in ("listings", "classifieds", "ads", "items", "results", "searchResults"):
        value = props.get(key)
        if isinstance(value, dict):
            value = value.get("items") or value.get("results") or value.get("data")
        if isinstance(value, list) and value:
            return [v for v in value if isinstance(v, dict)]
    return []


class LogicImmoScraper(BaseScraper):
    """Scraper for logic-immo.com — DataDome-protected HTML (AVIV group).

    SCAFFOLDING — like SeLoger/Leboncoin were on introduction. The transport,
    config and URL wiring are in place and reuse the shared Scrapfly Web Unlocker
    (``get_text``); the embedded-JSON contract is modelled on SeLoger's and MUST be
    validated on the first live run (DataDome blocks reconnaissance from a
    datacenter IP, so ``_normalize``/``_listings_from`` are provisional). No-op
    until ``LOGICIMMO_ENABLED`` is set and a transport is configured, so it never
    breaks a search; the scrape service also isolates it per source.

    Like SeLoger, cards carry no coordinates: no map marker, tagged with the
    searched commune's INSEE, excluded from radius search.
    """
    source = "logicimmo"

    BASE_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": f"{BASE}/",
    }

    last_cost: Optional[int] = None

    def _transport(self) -> Optional[Transport]:
        s = get_settings()
        if not s.logicimmo_enabled:
            return None
        if s.logicimmo_transport == "scrapfly":
            if not s.scrapfly_api_key:
                logger.info("logicimmo: transport scrapfly sans clé API — ignoré")
                return None
            return ScrapflyTransport(
                s.scrapfly_api_key, country=s.scrapfly_country,
                proxy_pool=s.scrapfly_proxy_pool, render_js=s.logicimmo_render_js,
                max_credits=s.scrapfly_max_credits_per_run,
            )
        if s.logicimmo_transport == "cookie":
            cookie = (s.logicimmo_datadome or "").strip()
            if not cookie:
                logger.info("logicimmo: transport cookie sans datadome — ignoré")
                return None
            return DirectCookieTransport(cookie, s.logicimmo_user_agent)
        logger.warning("logicimmo: transport inconnu %r — ignoré", s.logicimmo_transport)
        return None

    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        transport = self._transport()
        if transport is None or not criteria.communes:
            return []
        try:
            return await self._search_communes(transport, criteria)
        finally:
            self.last_cost = getattr(transport, "total_cost", None)
            await transport.aclose()

    async def _search_communes(
        self, transport: Transport, criteria: SearchCriteria
    ) -> List[NormalizedListing]:
        results: List[NormalizedListing] = []
        consecutive_failures = 0
        for commune in criteria.communes:
            seen: set[str] = set()
            hits = 0
            for type_slug in self._type_slugs(criteria):
                try:
                    ads = await self._fetch(transport, criteria, commune, type_slug, seen)
                except Exception as exc:  # noqa: BLE001 — isolate each request
                    logger.error("logicimmo: erreur %s/%s: %s", commune.insee, type_slug, exc)
                    ads = None
                if ads is None:  # transport failure — bail before tripping rate limits
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        logger.warning("logicimmo: 2 échecs consécutifs — stop")
                        return results
                    continue
                consecutive_failures = 0
                hits += len(ads)
                results.extend(ads)
                await asyncio.sleep(REQUEST_DELAY)
            logger.info("logicimmo: %s -> %d annonces", commune.name, hits)
        return results

    def _type_slugs(self, criteria: SearchCriteria) -> List[str]:
        if not criteria.property_types:
            return list(DEFAULT_TYPE_SLUGS)
        slugs = list(dict.fromkeys(
            TYPE_TO_SLUG[t] for t in criteria.property_types if t in TYPE_TO_SLUG
        ))
        return slugs or list(DEFAULT_TYPE_SLUGS)

    def _page_url(self, criteria: SearchCriteria, commune: Commune, type_slug: str) -> str:
        # PROVISIONAL slug — verify against a live Logic-Immo results URL.
        dist = DISTRIBUTION.get(criteria.transaction_type, "vente")
        slug = _slugify(commune.name)
        dept = _dept(commune.insee)
        return f"{BASE}/{dist}-{type_slug}-{slug}-{dept}/"

    async def _fetch(
        self, transport: Transport, criteria: SearchCriteria, commune: Commune,
        type_slug: str, seen: set,
    ) -> Optional[List[NormalizedListing]]:
        url = self._page_url(criteria, commune, type_slug)
        data = None
        for attempt in range(FIRST_PAGE_ATTEMPTS):
            html = await transport.get_text(url, self.BASE_HEADERS)
            data = extract_next_data(html) if html else None
            if data and _listings_from(data):
                break
            if attempt + 1 < FIRST_PAGE_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY)

        if not data:  # blocked, error or unparseable
            return None

        listings: List[NormalizedListing] = []
        for card in _listings_from(data):
            item = self._normalize(card, criteria, commune)
            if item and item.source_id not in seen:
                seen.add(item.source_id)
                listings.append(item)
        return listings

    def _normalize(self, card: dict, criteria: SearchCriteria,
                   commune: Commune) -> Optional[NormalizedListing]:
        """PROVISIONAL field mapping — confirm keys on the first live run."""
        cid = card.get("id") or card.get("reference") or card.get("aviId")
        if not cid:
            return None

        price = _to_float(card.get("price") or card.get("prix"))
        surface = _to_float(card.get("surface") or card.get("area") or card.get("livingArea"))
        raw_type = str(card.get("propertyType") or card.get("type") or "").lower()

        pictures = card.get("photos") or card.get("pictures") or card.get("images") or []
        pictures = [p.get("url") if isinstance(p, dict) else p for p in pictures]

        url = card.get("url") or card.get("permalink") or ""
        if url and url.startswith("/"):
            url = BASE + url

        return NormalizedListing(
            source=self.source,
            source_id=str(cid),
            title=card.get("title") or card.get("headline"),
            description=card.get("description"),
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            room=_to_int(card.get("rooms") or card.get("nbRooms")),
            bedroom=_to_int(card.get("bedrooms") or card.get("nbBedrooms")),
            property_type=LI_TYPE_TO_INT.get(raw_type),
            transaction_type=criteria.transaction_type,
            city_name=(card.get("city") or {}).get("name") if isinstance(card.get("city"), dict) else card.get("city") or commune.name,
            city_zipcode=card.get("zipCode") or card.get("postalCode") or commune.zipcode,
            city_insee=commune.insee,  # no INSEE on cards — normalized by scrape._apply
            department_code=_dept(commune.insee),
            energy_category=(card.get("energyClass") or card.get("dpe") or None),
            agency=card.get("agencyName") or card.get("agency"),
            url=url or None,
            pictures=[p for p in pictures if isinstance(p, str)],
        )
