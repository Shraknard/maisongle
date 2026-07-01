from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import urllib.parse
from typing import List, Optional

from app.config import get_settings
from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune
from app.scrapers.transport import Transport, DirectCookieTransport, ScrapflyTransport

logger = logging.getLogger("scrapers.seloger")

BASE = "https://www.seloger.com"
LIST_URL = f"{BASE}/list.htm"

MAX_PAGES = 5                   # refresh cap: newest pages / commune / run
MAX_PAGES_FIRST = 20            # first-scrape backfill cap / commune
REQUEST_DELAY = 1.0             # politeness pause between requests (seconds)
# DataDome occasionally "stealth-blocks": HTTP 200 with no listings. Each Scrapfly
# request uses a fresh residential IP, so an empty first page is retried a few
# times to ride out a transient block (a genuinely empty commune just costs these
# extra, bounded calls).
FIRST_PAGE_ATTEMPTS = 3
RETRY_DELAY = 2.0

# Our transaction int -> SeLoger `projects` code (2 = achat/vente, 1 = location).
PROJECT = {0: "2", 1: "1"}

# Our property type int -> SeLoger `types` code (PROVISIONAL — confirm live).
TYPE_TO_SL = {
    0: "1",   # appartement
    1: "2",   # maison / villa
    2: "9",   # parking / box
    3: "4",   # terrain
    4: "13",  # local commercial / boutique
    5: "6",   # immeuble
    6: "11",  # loft / atelier
}
DEFAULT_SL_TYPES = ["1", "2"]  # appartement + maison — the residential core

# SeLoger `estateType` label (lowercased, accent-stripped) -> our int (response
# side). Substring match, so "maison / villa" -> maison. PROVISIONAL.
SL_LABEL_TO_TYPE = {
    "appartement": 0, "studio": 0, "duplex": 0,
    "maison": 1, "villa": 1, "propriete": 1, "chateau": 1,
    "parking": 2, "box": 2, "garage": 2,
    "terrain": 3,
    "local": 4, "boutique": 4, "bureau": 4, "commerce": 4, "fonds": 4,
    "immeuble": 5,
    "loft": 6, "atelier": 6,
}

# ``window["initialData"] = JSON.parse("<js-escaped json>")`` — SeLoger embeds the
# whole result set as a JS string literal. DOTALL + greedy so it spans newlines and
# stops at the *last* ")" (escaped inner quotes are "\"", never a bare '")').
_INITIAL_DATA_RE = re.compile(r'window\["initialData"\]\s*=\s*JSON\.parse\("(.*)"\)', re.DOTALL)


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


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


def extract_initial_data(html: str) -> Optional[dict]:
    """Pull the ``window["initialData"] = JSON.parse("...")`` blob out of the HTML.

    The captured group is a JS string literal whose content is JSON. JSON string
    escapes are a superset of what SeLoger uses here, so we decode it in two steps:
    load the literal as a JSON string to recover the JSON text (handles ``\\"``,
    ``\\uXXXX``, ``\\n``), then load that text into the data object. This preserves
    accented French characters, unlike a bare ``unicode_escape`` decode.
    """
    m = _INITIAL_DATA_RE.search(html)
    if not m:
        return None
    try:
        inner = json.loads(f'"{m.group(1)}"')  # JS literal -> JSON text
        data = json.loads(inner)               # JSON text -> object
    except (ValueError, TypeError):
        logger.warning("seloger: initialData illisible")
        return None
    return data if isinstance(data, dict) else None


def classified_cards(data: dict) -> List[dict]:
    """Return the real listing cards (``cardType == "classified"``), skipping ads.

    The listings live at ``datasets[i]["cards"]["list"]``; we take the first
    dataset that carries any.
    """
    for ds in data.get("datasets") or []:
        cards = ((ds or {}).get("cards") or {}).get("list")
        if cards:
            return [
                c for c in cards
                if isinstance(c, dict) and c.get("cardType") == "classified"
            ]
    return []


def _total_pages(data: dict) -> Optional[int]:
    """Best-effort page count from the navigation block (shape PROVISIONAL)."""
    for ds in data.get("datasets") or []:
        nav = (ds or {}).get("navigation") or {}
        pagination = nav.get("pagination") or nav
        for key in ("pageCount", "totalPages", "pages"):
            total = pagination.get(key)
            if total:
                return _to_int(total)
    return None


def _price(pricing: dict) -> Optional[float]:
    """Prefer the numeric ``rawPrice``; fall back to parsing the formatted string."""
    if not isinstance(pricing, dict):
        return None
    raw = _to_float(pricing.get("rawPrice"))
    if raw:
        return raw
    for key in ("price", "monthlyPrice"):  # NOT squareMeterPrice (that's €/m²)
        val = pricing.get(key)
        if isinstance(val, str):
            digits = re.sub(r"[^\d]", "", val.split("€")[0])
            if digits:
                return float(digits)
    return None


def _property_type(card: dict) -> Optional[int]:
    label = _strip_accents((card.get("estateType") or "").lower())
    for key, val in SL_LABEL_TO_TYPE.items():
        if key in label:
            return val
    return None


def _epc(card: dict):
    """Energy / GHG class from ``epc`` (may be a bare letter or a dict). PROVISIONAL."""
    epc = card.get("epc")
    energy = ghg = None
    if isinstance(epc, str):
        energy = epc
    elif isinstance(epc, dict):
        energy = epc.get("energyValue") or epc.get("category") or epc.get("value")
        ghg = epc.get("gasValue") or epc.get("gesValue") or epc.get("ges")

    def _letter(v) -> Optional[str]:
        v = str(v or "").strip().upper()
        return v if v in ("A", "B", "C", "D", "E", "F", "G") else None

    return _letter(energy), _letter(ghg)


def _agency(card: dict) -> Optional[str]:
    contact = card.get("contact") or {}
    return contact.get("contactName") or contact.get("agencyName") or None


def _pictures(card: dict) -> List[str]:
    for key in ("photos", "pictures", "pictureUrls"):
        pics = card.get(key)
        if isinstance(pics, list):
            return [p for p in pics if isinstance(p, str)]
    return []


class SelogerScraper(BaseScraper):
    """Scraper for seloger.com — DataDome-protected HTML search pages.

    Like Leboncoin, SeLoger sits behind DataDome and is reached through a pluggable
    unblocking transport (Scrapfly Web Unlocker, recommended, or a manual cookie).
    Without one the scraper is a no-op, so it never breaks a search; the scrape
    service also isolates it per source.

    Flow: one ``list.htm`` request per commune (paginated), locating the
    ``window["initialData"]`` JSON blob and reading its listing cards. SeLoger
    search cards carry NO coordinates (those live only on detail pages), so — like
    PAP — listings have no map marker and are tagged with the searched commune's
    INSEE for the DB read filter.

    NOTE: the embedded-data contract (field names, `types`/`epc` shapes, pagination
    metadata) is reconstructed from public references and NOT yet validated live.
    Expect to adjust the mappings above once run against a real Scrapfly key.
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
        if criteria.first_scrape:
            logger.info("seloger: 1er scrape — backfill (jusqu'à %d pages/commune)",
                        MAX_PAGES_FIRST)
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
            try:
                ads = await self._fetch_commune(transport, criteria, commune)
            except Exception as exc:  # noqa: BLE001 — isolate each commune
                logger.error("seloger: erreur pour %s: %s", commune.insee, exc)
                ads = None
            if ads is None:  # transport failure — bail out before we trip rate limits
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    logger.warning("seloger: 2 échecs consécutifs — stop")
                    break
                continue
            consecutive_failures = 0
            logger.info("seloger: %s -> %d annonces", commune.name, len(ads))
            results.extend(ads)
        return results

    def _sl_types(self, criteria: SearchCriteria) -> List[str]:
        if not criteria.property_types:
            return DEFAULT_SL_TYPES
        # dict.fromkeys keeps order and dedups (e.g. local + immeuble differ, but
        # any future collisions collapse cleanly).
        out = list(dict.fromkeys(
            TYPE_TO_SL[t] for t in criteria.property_types if t in TYPE_TO_SL
        ))
        return out or DEFAULT_SL_TYPES

    def _page_url(self, criteria: SearchCriteria, commune: Commune, page: int) -> str:
        # SeLoger filters by INSEE via the JSON `places` param; this avoids having
        # to slugify the city (unlike PAP's path-based URLs).
        places = json.dumps([{"inseeCodes": [commune.insee]}], separators=(",", ":"))
        params = {
            "projects": PROJECT.get(criteria.transaction_type, "2"),
            "types": ",".join(self._sl_types(criteria)),
            "places": places,
            "enterprise": "0",
            "qsVersion": "1.0",
            "LISTING-LISTpg": str(page),
        }
        if criteria.budget_min is not None or criteria.budget_max is not None:
            params["price"] = f"{criteria.budget_min or 0}/{criteria.budget_max or 'NaN'}"
        if criteria.surface_min is not None or criteria.surface_max is not None:
            params["surface"] = f"{criteria.surface_min or 0}/{criteria.surface_max or 'NaN'}"
        if criteria.room_min is not None or criteria.room_max is not None:
            # SeLoger rooms is an enum list (1..5, where 5 means "5+").
            lo = max(criteria.room_min or 1, 1)
            hi = min(criteria.room_max or 5, 5)
            params["rooms"] = ",".join(str(r) for r in range(lo, hi + 1)) or "5"
        return LIST_URL + "?" + urllib.parse.urlencode(params)

    async def _fetch_commune(
        self, transport: Transport, criteria: SearchCriteria, commune: Commune,
    ) -> Optional[List[NormalizedListing]]:
        """Page through one commune's results. Returns None on transport failure
        (vs [] for a genuinely empty commune) so the caller can bail on a block."""
        listings: List[NormalizedListing] = []
        seen: set[str] = set()
        max_pages = MAX_PAGES_FIRST if criteria.first_scrape else MAX_PAGES

        for page in range(1, max_pages + 1):
            url = self._page_url(criteria, commune, page)
            # Retry an empty/failed first page (likely a DataDome stealth-block);
            # later pages are taken at face value.
            attempts = FIRST_PAGE_ATTEMPTS if page == 1 else 1
            data = None
            for attempt in range(attempts):
                html = await transport.get_text(url, self.BASE_HEADERS)
                data = extract_initial_data(html) if html else None
                if data and classified_cards(data):
                    break
                if attempt + 1 < attempts:
                    await asyncio.sleep(RETRY_DELAY)

            if not data:  # blocked, error or unparseable
                if page == 1:
                    return None  # first page failed → signal transport failure
                break

            cards = classified_cards(data)
            if not cards:
                break
            for card in cards:
                item = self._normalize(card, criteria, commune)
                if item and item.source_id not in seen:
                    seen.add(item.source_id)
                    listings.append(item)

            total_pages = _total_pages(data)
            if total_pages is not None and page >= total_pages:
                break
            await asyncio.sleep(REQUEST_DELAY)
        return listings

    def _normalize(self, card: dict, criteria: SearchCriteria,
                   commune: Commune) -> Optional[NormalizedListing]:
        cid = card.get("id")
        if not cid:
            return None

        pricing = card.get("pricing") or {}
        price = _price(pricing)
        surface = _to_float(card.get("surface"))
        energy, ghg = _epc(card)

        return NormalizedListing(
            source=self.source,
            source_id=str(cid),
            title=card.get("title"),
            description=card.get("description"),
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            room=_to_int(card.get("rooms")),
            bedroom=_to_int(card.get("bedrooms")),
            property_type=_property_type(card),
            transaction_type=criteria.transaction_type,
            city_name=card.get("cityLabel") or commune.name,
            city_zipcode=card.get("zipCode") or commune.zipcode,
            # Tagged with the searched commune (SeLoger search cards have no INSEE
            # and no coordinates). normalize_insee in scrape._apply maps PLM
            # arrondissements to the parent commune.
            city_insee=commune.insee,
            department_code=_dept(commune.insee),
            energy_category=energy,
            ghg_category=ghg,
            agency=_agency(card),
            url=card.get("classifiedURL"),
            pictures=_pictures(card),
        )
