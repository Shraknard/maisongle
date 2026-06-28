from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from app.config import get_settings
from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune
from app.scrapers.transport import (
    JsonTransport, DirectCookieTransport, ScrapflyTransport,
)

logger = logging.getLogger("scrapers.leboncoin")

SEARCH_URL = "https://api.leboncoin.fr/finder/search"
# Public web client key shipped in the leboncoin SPA bundle (not a secret).
API_KEY = "ba0c2dad52b3ec"

RESULTS_PER_PAGE = 35           # leboncoin's own page size
MAX_PAGES = 8                   # personal-use cap: ~280 ads / commune / run
REQUEST_DELAY = 1.0             # politeness pause between requests (seconds)

# Our int transaction type -> leboncoin category id (9 = ventes, 10 = locations).
CATEGORY = {0: "9", 1: "10"}

# Our int property type -> leboncoin real_estate_type enum value.
TYPE_TO_LBC = {
    0: "2",  # appartement
    1: "1",  # maison
    2: "4",  # parking
    3: "3",  # terrain
    4: "5",  # local commercial -> "Autre"
    5: "5",  # immeuble -> "Autre"
    6: "2",  # loft -> appartement
}
DEFAULT_LBC_TYPES = ["1", "2"]  # maison + appartement — the residential core

# leboncoin real_estate_type value -> our int property type (response side).
LBC_TO_TYPE = {"1": 1, "2": 0, "3": 3, "4": 2, "5": 4}


def _dept(insee: Optional[str]) -> str:
    insee = (insee or "").zfill(5)
    return insee[:3] if insee[:2] in ("97", "98") else insee[:2]


def _attrs(ad: dict) -> Dict[str, str]:
    """Flatten leboncoin's ``attributes`` list into a {key: value} dict."""
    out: Dict[str, str] = {}
    for attr in ad.get("attributes") or []:
        key = attr.get("key")
        if key is not None and attr.get("value") is not None:
            out[key] = attr.get("value")
    return out


def _first(value):
    """leboncoin returns price as a single-element list (range otherwise)."""
    if isinstance(value, list):
        nums = [v for v in value if isinstance(v, (int, float))]
        return min(nums) if nums else None
    return value


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


class LeboncoinScraper(BaseScraper):
    """Scraper for leboncoin.fr — JSON ``finder/search`` API behind DataDome.

    DataDome blocks the API unless the request is fronted by an unblocking
    transport (see ``transport.py``): Scrapfly's Web Unlocker (residential IP +
    ASP, recommended) or a manually injected ``datadome`` cookie (fallback). The
    transport is chosen from settings; without one the scraper is a no-op, so it
    never breaks a search, and the scrape service isolates it per source anyway.

    Flow: one ``finder/search`` per commune (paginated), tagging each ad with the
    searched commune's INSEE so the DB read filter (``city_insee``) matches — same
    contract as PAP. Unlike PAP, leboncoin ads carry coordinates, so they also
    appear on the map and in radius searches.
    """
    source = "leboncoin"

    # App headers leboncoin's SPA sends with every finder/search call. The
    # transport adds whatever it needs on top (cookie, or its own UA/fingerprint).
    BASE_HEADERS = {
        "Accept": "*/*",
        "api_key": API_KEY,
        "Origin": "https://www.leboncoin.fr",
        "Referer": "https://www.leboncoin.fr/recherche",
    }

    def _transport(self) -> Optional[JsonTransport]:
        """Build the configured transport, or None if the source is disabled."""
        s = get_settings()
        if not s.leboncoin_enabled:
            return None
        if s.leboncoin_transport == "scrapfly":
            if not s.scrapfly_api_key:
                logger.info("leboncoin: transport scrapfly sans clé API — ignoré")
                return None
            return ScrapflyTransport(
                s.scrapfly_api_key, country=s.scrapfly_country,
                proxy_pool=s.scrapfly_proxy_pool, render_js=s.scrapfly_render_js,
            )
        if s.leboncoin_transport == "cookie":
            cookie = (s.leboncoin_datadome or "").strip()
            if not cookie:
                logger.info("leboncoin: transport cookie sans datadome — ignoré")
                return None
            return DirectCookieTransport(cookie, s.leboncoin_user_agent)
        logger.warning("leboncoin: transport inconnu %r — ignoré", s.leboncoin_transport)
        return None

    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        transport = self._transport()
        if transport is None or not criteria.communes:
            return []

        results: List[NormalizedListing] = []
        try:
            for commune in criteria.communes:
                try:
                    ads = await self._fetch_commune(transport, criteria, commune)
                    logger.info("leboncoin: %s -> %d annonces", commune.name, len(ads))
                    results.extend(ads)
                except Exception as exc:  # noqa: BLE001 — isolate each commune
                    logger.error("leboncoin: erreur pour %s: %s", commune.insee, exc)
        finally:
            await transport.aclose()
        return results

    def _lbc_types(self, criteria: SearchCriteria) -> List[str]:
        if not criteria.property_types:
            return DEFAULT_LBC_TYPES
        # dict.fromkeys keeps order and dedups (e.g. local + immeuble both -> "5").
        out = list(dict.fromkeys(
            TYPE_TO_LBC[t] for t in criteria.property_types if t in TYPE_TO_LBC
        ))
        return out or DEFAULT_LBC_TYPES

    def _build_filters(self, criteria: SearchCriteria, commune: Commune) -> dict:
        ranges: dict = {}
        if criteria.budget_min is not None or criteria.budget_max is not None:
            ranges["price"] = {}
            if criteria.budget_min is not None:
                ranges["price"]["min"] = criteria.budget_min
            if criteria.budget_max is not None:
                ranges["price"]["max"] = criteria.budget_max
        if criteria.surface_min is not None or criteria.surface_max is not None:
            ranges["square"] = {}
            if criteria.surface_min is not None:
                ranges["square"]["min"] = criteria.surface_min
            if criteria.surface_max is not None:
                ranges["square"]["max"] = criteria.surface_max
        if criteria.room_min is not None or criteria.room_max is not None:
            ranges["rooms"] = {}
            if criteria.room_min is not None:
                ranges["rooms"]["min"] = criteria.room_min
            if criteria.room_max is not None:
                ranges["rooms"]["max"] = criteria.room_max

        return {
            "category": {"id": CATEGORY.get(criteria.transaction_type, "9")},
            "enums": {
                "ad_type": ["offer"],
                "real_estate_type": self._lbc_types(criteria),
            },
            "location": {
                "locations": [{
                    "locationType": "city",
                    "city": commune.name,
                    "zipcode": commune.zipcode or "",
                    "department_id": _dept(commune.insee),
                    "lat": commune.latitude,
                    "lng": commune.longitude,
                    "radius": 0,
                }],
            },
            "ranges": ranges,
        }

    async def _fetch_commune(
        self, transport: JsonTransport, criteria: SearchCriteria, commune: Commune,
    ) -> List[NormalizedListing]:
        listings: List[NormalizedListing] = []
        seen: set[str] = set()
        filters = self._build_filters(criteria, commune)

        for page in range(MAX_PAGES):
            payload = {
                "filters": filters,
                "limit": RESULTS_PER_PAGE,
                "limit_alu": 0,
                "offset": page * RESULTS_PER_PAGE,
                "sort_by": "time",
                "sort_order": "desc",
            }
            data = await transport.post_json(SEARCH_URL, payload, self.BASE_HEADERS)
            if not data:  # blocked, error or empty — transport already logged
                break

            ads = data.get("ads") or []
            if not ads:
                break
            for ad in ads:
                item = self._normalize(ad, criteria, commune)
                if item and item.source_id not in seen:
                    seen.add(item.source_id)
                    listings.append(item)

            total = data.get("total") or 0
            if (page + 1) * RESULTS_PER_PAGE >= total:
                break
            await asyncio.sleep(REQUEST_DELAY)
        return listings

    def _normalize(self, ad: dict, criteria: SearchCriteria,
                   commune: Commune) -> Optional[NormalizedListing]:
        list_id = ad.get("list_id")
        if not list_id:
            return None

        attrs = _attrs(ad)
        loc = ad.get("location") or {}
        owner = ad.get("owner") or {}
        images = ad.get("images") or {}

        price = _first(ad.get("price"))
        surface = _to_float(attrs.get("square"))
        rooms = _to_int(attrs.get("rooms"))
        property_type = LBC_TO_TYPE.get(attrs.get("real_estate_type"))

        url = ad.get("url") or ""
        if url and url.startswith("/"):
            url = "https://www.leboncoin.fr" + url

        pictures = images.get("urls_large") or images.get("urls") or []
        agency = owner.get("name")
        if owner.get("type") == "private":
            agency = "Particulier"

        return NormalizedListing(
            source=self.source,
            source_id=str(list_id),
            title=ad.get("subject"),
            description=ad.get("body"),
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            land_surface=_to_float(attrs.get("land_plot_surface")),
            room=rooms,
            bedroom=_to_int(attrs.get("bedrooms")),
            floor=_to_int(attrs.get("floor_number")),
            property_type=property_type,
            transaction_type=criteria.transaction_type,
            city_name=loc.get("city") or commune.name,
            city_zipcode=loc.get("zipcode") or commune.zipcode,
            city_insee=commune.insee,  # normalized to parent commune by scrape._apply
            department_code=loc.get("department_id") or _dept(commune.insee),
            latitude=_to_float(loc.get("lat")),
            longitude=_to_float(loc.get("lng")),
            energy_category=(attrs.get("energy_rate") or "").upper() or None,
            ghg_category=(attrs.get("ges") or "").upper() or None,
            agency=agency,
            url=url or f"https://www.leboncoin.fr/ad/ventes_immobilieres/{list_id}",
            pictures=[u for u in pictures if u],
        )
