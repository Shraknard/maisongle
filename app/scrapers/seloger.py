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

logger = logging.getLogger("scrapers.seloger")

BASE = "https://www.seloger.com"

REQUEST_DELAY = 1.0             # politeness pause between requests (seconds)
# SeLoger's SERP is an SPA: the server renders only the first 30 results (page 1)
# for SEO; deeper pages load client-side from an internal "classified-search" API,
# and no URL param paginates the server HTML (LISTING-LISTpg/pg/page are ignored).
# We therefore take the first page per (commune, type). DataDome occasionally
# "stealth-blocks" (HTTP 200 with no listings); each Scrapfly request uses a fresh
# residential IP, so we retry a few times to ride out a transient block.
FIRST_PAGE_ATTEMPTS = 3
RETRY_DELAY = 2.0

# Our transaction int -> SeLoger URL distribution segment.
DISTRIBUTION = {0: "achat", 1: "location"}

# Our property type int -> SeLoger URL type slug (``bien-<slug>``). One type per
# URL — the results page filters by a single ``bien-…`` path segment.
TYPE_TO_SLUG = {
    0: "appartement",
    1: "maison",
    2: "parking",
    3: "terrain",
    4: "local-commercial",
    5: "immeuble",
    6: "loft",
}
# ``[]`` (tous types) falls back to the residential core — the only reliably
# validated slugs; read-side filtering (routers/search) narrows further anyway.
DEFAULT_TYPE_SLUGS = ["appartement", "maison"]

# SeLoger ``rawData.propertyType`` enum -> our int (response side).
SL_PTYPE_TO_INT = {
    "APARTMENT": 0,
    "HOUSE": 1, "VILLA": 1, "PROPERTY": 1, "CASTLE": 1, "MANSION": 1,
    "PARKING": 2, "GARAGE": 2, "BOX": 2,
    "LAND": 3, "TERRAIN": 3,
    "SHOP": 4, "OFFICE": 4, "BUSINESS": 4, "PREMISES": 4, "COMMERCIAL": 4,
    "OFFICES": 4, "STORE": 4,
    "BUILDING": 5,
    "LOFT": 6, "ATELIER": 6,
}

# SeLoger embeds the whole result set as a JS string literal:
#   window["__UFRN_FETCHER__"] = JSON.parse("<js-escaped json>")
# (this replaced the older ``window["initialData"]`` blob). The listings live at
# ``data["<serp-service>"]["pageProps"]``.
_FETCHER_MARKER = '__UFRN_FETCHER__'
_JS_PARSE = 'JSON.parse("'


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _slugify(name: str) -> str:
    """City name -> SeLoger URL slug ('Saint-Étienne' -> 'saint-etienne')."""
    s = _strip_accents(name or "").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


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


def _letter(value) -> Optional[str]:
    v = str(value or "").strip().upper()
    return v if v in ("A", "B", "C", "D", "E", "F", "G") else None


def extract_fetcher_data(html: str) -> Optional[dict]:
    """Pull the ``window["__UFRN_FETCHER__"] = JSON.parse("...")`` blob out of the HTML.

    The captured group is a JS string literal whose content is JSON, decoded in two
    steps (JS literal -> JSON text -> object) so ``\\"``/``\\uXXXX``/``\\n`` and
    accented French characters all survive. Located by string search rather than a
    regex because the blob is ~1 MB and there are several ``JSON.parse`` scripts.
    """
    i = html.find(f'id="{_FETCHER_MARKER}"')
    if i == -1:
        return None
    p = html.find(_JS_PARSE, i)
    if p == -1:
        return None
    start = p + len(_JS_PARSE)
    end = html.find("</script>", start)
    if end == -1:
        return None
    seg = html[start:end].rstrip().rstrip(";").rstrip()
    if not seg.endswith('")'):
        logger.warning("seloger: fin de blob __UFRN_FETCHER__ inattendue")
        return None
    seg = seg[:-2]  # drop the trailing ")
    try:
        inner = json.loads(f'"{seg}"')  # JS literal -> JSON text
        data = json.loads(inner)        # JSON text -> object
    except (ValueError, TypeError):
        logger.warning("seloger: __UFRN_FETCHER__ illisible")
        return None
    return data if isinstance(data, dict) else None


def _page_props(data: dict) -> Optional[dict]:
    """The SERP service's ``pageProps`` (holds the classifieds id list + card data)."""
    for value in (data.get("data") or {}).values():
        if isinstance(value, dict):
            pp = value.get("pageProps")
            if isinstance(pp, dict) and "classifieds" in pp and "classifiedsData" in pp:
                return pp
    return None


def classified_cards(data: dict) -> List[dict]:
    """Return the listing cards, resolving each id in ``classifieds`` against the
    ``classifiedsData`` map."""
    pp = _page_props(data)
    if not pp:
        return []
    by_id = pp.get("classifiedsData") or {}
    cards = []
    for cid in pp.get("classifieds") or []:
        card = by_id.get(cid)
        if isinstance(card, dict):
            cards.append(card)
    return cards


def _property_type(card: dict) -> Optional[int]:
    raw = (card.get("rawData") or {}).get("propertyType")
    return SL_PTYPE_TO_INT.get(str(raw or "").upper())


def _floor(card: dict) -> Optional[int]:
    """Best-effort floor from the ``numberOfFloors`` hard fact ('Étage 2/4' -> 2,
    'RDC/2' -> 0)."""
    for fact in (card.get("hardFacts") or {}).get("facts") or []:
        if fact.get("type") == "numberOfFloors":
            value = str(fact.get("value") or "")
            if "RDC" in value.upper():
                return 0
            m = re.search(r"\d+", value)
            return int(m.group()) if m else None
    return None


def _agency(card: dict) -> Optional[str]:
    provider = card.get("provider") or {}
    if provider.get("isPrivateOwner"):
        return None
    inter = provider.get("intermediaryCard") or {}
    return inter.get("title") or (card.get("cardProvider") or {}).get("title") or None


def _pictures(card: dict) -> List[str]:
    images = (card.get("gallery") or {}).get("images") or []
    return [im.get("url") for im in images
            if isinstance(im, dict) and isinstance(im.get("url"), str)]


class SelogerScraper(BaseScraper):
    """Scraper for seloger.com — DataDome-protected HTML search pages.

    Like Leboncoin, SeLoger sits behind DataDome and is reached through a pluggable
    unblocking transport (Scrapfly Web Unlocker, recommended, or a manual cookie).
    Without one the scraper is a no-op, so it never breaks a search; the scrape
    service also isolates it per source.

    Flow: SeLoger's ``list.htm`` query endpoint is dead (500s), so we hit the slug
    results pages — ``/immobilier/{achat|location}/immo-<city>-<dept>/bien-<type>/``
    — one request per (commune, type). The results are embedded as
    ``window["__UFRN_FETCHER__"] = JSON.parse("…")``; listings sit in the SERP
    service's ``pageProps.classifieds`` (id list) resolved against
    ``pageProps.classifiedsData``. Only the first page (30 cards) is reachable — the
    SERP is an SPA that loads further pages from an internal API — so SeLoger
    contributes the 30 newest listings per (commune, type).

    SeLoger search cards carry NO coordinates and NO INSEE (only a slug-resolved
    geo hierarchy), so — like PAP — listings have no map marker and are tagged with
    the searched commune's INSEE; SeLoger is excluded from radius search. Server-side
    budget/surface/room filters are skipped (the read side filters the DB), so the
    URL only pins location + property type.
    """
    source = "seloger"

    BASE_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": f"{BASE}/",
    }

    # Scrapfly credits billed by the last search() run (None if not via Scrapfly),
    # surfaced into scrape_runs.detail by the scrape service.
    last_cost: Optional[int] = None

    def _transport(self) -> Optional[Transport]:
        """Build the configured transport, or None if the source is disabled."""
        s = get_settings()
        if not s.seloger_enabled:
            return None
        if s.seloger_transport == "scrapfly":
            if not s.scrapfly_api_key:
                logger.info("seloger: transport scrapfly sans clé API — ignoré")
                return None
            return ScrapflyTransport(
                s.scrapfly_api_key, country=s.scrapfly_country,
                proxy_pool=s.scrapfly_proxy_pool, render_js=s.seloger_render_js,
                max_credits=s.scrapfly_max_credits_per_run,
            )
        if s.seloger_transport == "cookie":
            cookie = (s.seloger_datadome or "").strip()
            if not cookie:
                logger.info("seloger: transport cookie sans datadome — ignoré")
                return None
            return DirectCookieTransport(cookie, s.seloger_user_agent)
        logger.warning("seloger: transport inconnu %r — ignoré", s.seloger_transport)
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
            commune_hits = 0
            for type_slug in self._type_slugs(criteria):
                try:
                    ads = await self._fetch(transport, criteria, commune, type_slug, seen)
                except Exception as exc:  # noqa: BLE001 — isolate each request
                    logger.error("seloger: erreur pour %s/%s: %s",
                                 commune.insee, type_slug, exc)
                    ads = None
                if ads is None:  # transport failure — bail before we trip rate limits
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        logger.warning("seloger: 2 échecs consécutifs — stop")
                        return results
                    continue
                consecutive_failures = 0
                commune_hits += len(ads)
                results.extend(ads)
                await asyncio.sleep(REQUEST_DELAY)  # politeness between requests
            logger.info("seloger: %s -> %d annonces", commune.name, commune_hits)
        return results

    def _type_slugs(self, criteria: SearchCriteria) -> List[str]:
        if not criteria.property_types:
            return list(DEFAULT_TYPE_SLUGS)
        slugs = list(dict.fromkeys(
            TYPE_TO_SLUG[t] for t in criteria.property_types if t in TYPE_TO_SLUG
        ))
        return slugs or list(DEFAULT_TYPE_SLUGS)

    def _page_url(self, criteria: SearchCriteria, commune: Commune,
                  type_slug: str) -> str:
        dist = DISTRIBUTION.get(criteria.transaction_type, "achat")
        slug = _slugify(commune.name)
        dept = _dept(commune.insee)
        return f"{BASE}/immobilier/{dist}/immo-{slug}-{dept}/bien-{type_slug}/"

    async def _fetch(
        self, transport: Transport, criteria: SearchCriteria, commune: Commune,
        type_slug: str, seen: set,
    ) -> Optional[List[NormalizedListing]]:
        """Fetch one (commune, type) — the first results page only (see the module
        note). Returns None on transport failure (vs [] for a genuinely empty
        result) so the caller can bail on a block; an empty first page is retried a
        few times to ride out a DataDome stealth-block."""
        url = self._page_url(criteria, commune, type_slug)
        data = None
        for attempt in range(FIRST_PAGE_ATTEMPTS):
            html = await transport.get_text(url, self.BASE_HEADERS)
            data = extract_fetcher_data(html) if html else None
            if data and classified_cards(data):
                break
            if attempt + 1 < FIRST_PAGE_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY)

        if not data:  # blocked, error or unparseable
            return None

        listings: List[NormalizedListing] = []
        for card in classified_cards(data):
            item = self._normalize(card, criteria, commune)
            if item and item.source_id not in seen:
                seen.add(item.source_id)
                listings.append(item)
        return listings

    def _normalize(self, card: dict, criteria: SearchCriteria,
                   commune: Commune) -> Optional[NormalizedListing]:
        cid = card.get("id")
        if not cid:
            return None

        raw = card.get("rawData") or {}
        price = _to_float(raw.get("price"))
        surface = _to_float((raw.get("surface") or {}).get("main"))
        land_surface = _to_float((raw.get("surface") or {}).get("plot"))
        address = (card.get("location") or {}).get("address") or {}
        description = card.get("mainDescription") or {}
        title = description.get("headline") or (card.get("hardFacts") or {}).get("title")

        return NormalizedListing(
            source=self.source,
            source_id=str(cid),
            title=title,
            description=description.get("description"),
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            land_surface=land_surface,
            room=_to_int(raw.get("nbroom")),
            bedroom=_to_int(raw.get("nbbedroom")),
            floor=_floor(card),
            property_type=_property_type(card),
            transaction_type=criteria.transaction_type,
            city_name=address.get("city") or commune.name,
            city_zipcode=address.get("zipCode") or commune.zipcode,
            # Tagged with the searched commune (SeLoger search cards have no INSEE
            # and no coordinates). normalize_insee in scrape._apply maps PLM
            # arrondissements to the parent commune.
            city_insee=commune.insee,
            department_code=_dept(commune.insee),
            energy_category=_letter(card.get("energyClass")),
            # GHG/GES is only on detail pages, not search cards.
            ghg_category=None,
            agency=_agency(card),
            url=card.get("url"),
            pictures=_pictures(card),
        )
