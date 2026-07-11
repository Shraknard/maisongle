from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from app.config import get_settings
from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune
from app.scrapers.transport import (
    Transport, DirectCookieTransport, ScrapflyTransport,
)

logger = logging.getLogger("scrapers.leboncoin")

SEARCH_URL = "https://api.leboncoin.fr/finder/search"
# Public web client key shipped in the leboncoin SPA bundle (not a secret).
API_KEY = "ba0c2dad52b3ec"

RESULTS_PER_PAGE = 35           # leboncoin's own page size
# leboncoin's finder matches by a radius around a point (the "city" filter is a
# point + radius, not a commune polygon), so a commune search pulls in nearby
# communes within this radius — "commune et alentours", like PAP's proximity.
COMMUNE_RADIUS_M = 10000
MAX_PAGES = 5                   # refresh cap: ~175 newest ads / commune / run
# First scrape of a perimeter backfills the existing catalogue. leboncoin's
# finder hard-caps pagination at max_pages=100 (~3500 ads), the most reachable
# for one query — filtered searches return fewer and stop early at their total.
MAX_PAGES_FIRST = 100
REQUEST_DELAY = 1.0             # politeness pause between requests (seconds)
# DataDome occasionally "stealth-blocks": HTTP 200 with an empty result set
# instead of a 403. Each Scrapfly request uses a fresh residential IP, so an
# empty first page is retried a few times to ride out a transient block (a truly
# empty perimeter just costs these extra calls, bounded and rare).
FIRST_PAGE_ATTEMPTS = 3
RETRY_DELAY = 2.0

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

    # Scrapfly credits billed by the last search() run (None if not via Scrapfly),
    # surfaced into scrape_runs.detail by the scrape service.
    last_cost: Optional[int] = None

    def _transport(self) -> Optional[Transport]:
        """Build the configured transport, or None if the source is disabled."""
        s = get_settings()
        if not s.leboncoin_enabled:
            return None
        if s.leboncoin_transport == "scrapfly":
            if not s.scrapfly_api_key:
                logger.info("leboncoin: transport scrapfly sans clé API — ignoré")
                return None
            # render_js stays False: Scrapfly rejects JS rendering on POST, and
            # the finder is a JSON endpoint that needs no rendering anyway.
            return ScrapflyTransport(
                s.scrapfly_api_key, country=s.scrapfly_country,
                proxy_pool=s.scrapfly_proxy_pool,
                max_credits=s.scrapfly_max_credits_per_run,
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
        if criteria.first_scrape:
            logger.info("leboncoin: 1er scrape — backfill (jusqu'à %d pages/périmètre)",
                        MAX_PAGES_FIRST)

        results: List[NormalizedListing] = []
        try:
            if criteria.is_radius:
                # One area search at the centre — far cheaper than one request per
                # enumerated commune. Ads carry coordinates, so the DB read trims to
                # the exact circle by haversine (no INSEE tag needed).
                location = self._area_location(
                    criteria.center_lat, criteria.center_lon, criteria.radius_km
                )
                ads = await self._fetch(transport, criteria, location, insee=None)
                if ads:
                    logger.info("leboncoin: rayon -> %d annonces", len(ads))
                    results.extend(ads)
            else:
                results = await self._search_communes(transport, criteria)
        finally:
            self.last_cost = getattr(transport, "total_cost", None)
            await transport.aclose()
        return results

    async def _search_communes(
        self, transport: Transport, criteria: SearchCriteria
    ) -> List[NormalizedListing]:
        results: List[NormalizedListing] = []
        consecutive_failures = 0
        for commune in criteria.communes:
            try:
                location = self._city_location(commune)
                ads = await self._fetch(transport, criteria, location, insee=commune.insee)
            except Exception as exc:  # noqa: BLE001 — isolate each commune
                logger.error("leboncoin: erreur pour %s: %s", commune.insee, exc)
                ads = None
            if ads is None:  # transport failure — bail out before we trip rate limits
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    logger.warning("leboncoin: 2 échecs consécutifs — stop")
                    break
                continue
            consecutive_failures = 0
            logger.info("leboncoin: %s -> %d annonces", commune.name, len(ads))
            results.extend(ads)
        return results

    def _lbc_types(self, criteria: SearchCriteria) -> List[str]:
        if not criteria.property_types:
            return DEFAULT_LBC_TYPES
        # dict.fromkeys keeps order and dedups (e.g. local + immeuble both -> "5").
        out = list(dict.fromkeys(
            TYPE_TO_LBC[t] for t in criteria.property_types if t in TYPE_TO_LBC
        ))
        return out or DEFAULT_LBC_TYPES

    @staticmethod
    def _city_location(commune: Commune) -> dict:
        """leboncoin location entry for a commune: a point + radius (not a polygon).

        The lat/lng/radius must be nested under ``area`` and paired with a label —
        a flat ``{city, lat, lng}`` is silently ignored and returns 0 results.
        """
        return {
            "locationType": "city",
            "city": commune.name,
            "label": f"{commune.name} (toute la ville)",
            "area": {
                "lat": commune.latitude,
                "lng": commune.longitude,
                "radius": COMMUNE_RADIUS_M,
            },
        }

    @staticmethod
    def _area_location(lat: float, lon: float, radius_km: float) -> dict:
        """leboncoin location entry for a radius search (point + radius in metres)."""
        return {
            "locationType": "city",
            "area": {"lat": lat, "lng": lon, "radius": int(radius_km * 1000)},
        }

    def _build_filters(self, criteria: SearchCriteria, location: dict) -> dict:
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
            "location": {"locations": [location]},
            "ranges": ranges,
        }

    async def _fetch(
        self, transport: Transport, criteria: SearchCriteria,
        location: dict, insee: Optional[str],
    ) -> Optional[List[NormalizedListing]]:
        """Fetch ads for one location (commune or radius), tagging each with
        ``insee``. Returns None on transport failure (vs [] for no ads) so the
        caller can distinguish a block from a genuinely empty perimeter."""
        listings: List[NormalizedListing] = []
        seen: set[str] = set()
        filters = self._build_filters(criteria, location)
        max_pages = MAX_PAGES_FIRST if criteria.first_scrape else MAX_PAGES

        for page in range(max_pages):
            payload = {
                "filters": filters,
                "limit": RESULTS_PER_PAGE,
                "limit_alu": 0,
                "offset": page * RESULTS_PER_PAGE,
                "sort_by": "time",
                "sort_order": "desc",
            }
            # Retry an empty/failed first page (likely a DataDome stealth-block);
            # later pages are taken at face value.
            attempts = FIRST_PAGE_ATTEMPTS if page == 0 else 1
            data = None
            for attempt in range(attempts):
                data = await transport.post_json(SEARCH_URL, payload, self.BASE_HEADERS)
                if data and data.get("ads"):
                    break
                if attempt + 1 < attempts:
                    await asyncio.sleep(RETRY_DELAY)

            if not data:  # blocked, error or empty — transport already logged
                if page == 0:
                    return None  # first page failed → signal transport failure
                break

            ads = data.get("ads") or []
            if not ads:
                break
            for ad in ads:
                item = self._normalize(ad, criteria, insee)
                if item and item.source_id not in seen:
                    seen.add(item.source_id)
                    listings.append(item)

            total = data.get("total") or 0
            if (page + 1) * RESULTS_PER_PAGE >= total:
                break
            await asyncio.sleep(REQUEST_DELAY)
        return listings

    def _normalize(self, ad: dict, criteria: SearchCriteria,
                   insee: Optional[str]) -> Optional[NormalizedListing]:
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
            city_name=loc.get("city"),
            city_zipcode=loc.get("zipcode"),
            # Tag with the searched commune (None for radius). leboncoin gives no
            # INSEE, and its radius match bleeds into nearby communes, so all hits
            # are tagged with the searched commune — like PAP. normalize_insee in
            # scrape._apply maps it to the parent commune.
            city_insee=insee,
            department_code=loc.get("department_id"),
            latitude=_to_float(loc.get("lat")),
            longitude=_to_float(loc.get("lng")),
            energy_category=(attrs.get("energy_rate") or "").upper() or None,
            ghg_category=(attrs.get("ges") or "").upper() or None,
            agency=agency,
            url=url or f"https://www.leboncoin.fr/ad/ventes_immobilieres/{list_id}",
            pictures=[u for u in pictures if u],
        )
