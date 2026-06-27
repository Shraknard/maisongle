from __future__ import annotations

import json
import logging
from typing import List, Optional

import httpx

from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune

logger = logging.getLogger("scrapers.bienici")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
SUGGEST_URL = "https://res.bienici.com/suggest.json"
ADS_URL = "https://www.bienici.com/realEstateAds.json"

RESULTS_PER_PAGE = 100
MAX_PAGES = 5  # personal-use cap: up to 500 ads / commune / run

# Melo-style int <-> Bien'ici string property types
PROPERTY_TYPE_TO_BIENICI = {
    0: "flat", 1: "house", 2: "parking", 3: "terrain",
    4: "premises", 5: "building", 6: "loft",
}
BIENICI_TO_PROPERTY_TYPE = {
    "flat": 0, "duplex": 0, "loft": 6,
    "house": 1, "villa": 1, "townhouse": 1, "castle": 1, "manor": 1,
    "parking": 2, "terrain": 3,
    "premises": 4, "shop": 4, "office": 4, "store": 4,
    "building": 5,
}
DEFAULT_BIENICI_TYPES = [
    "house", "flat", "loft", "townhouse", "castle",
    "building", "parking", "terrain", "premises",
]


def _num(value):
    """Bien'ici returns price/area as a list for ranges (e.g. new builds). Take the lowest."""
    if isinstance(value, list):
        nums = [v for v in value if isinstance(v, (int, float))]
        return min(nums) if nums else None
    return value


class BienIciScraper(BaseScraper):
    """Scraper for bienici.com — open JSON endpoints, no anti-bot.

    Flow: resolve each commune to Bien'ici ``zoneIds`` via the suggest endpoint,
    then page through ``realEstateAds.json`` filtered by those zones.
    """
    source = "bienici"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bienici.com/",
            "Accept": "application/json",
        }

    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        results: List[NormalizedListing] = []
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            for commune in criteria.communes:
                try:
                    zone_ids = await self._zone_ids(client, commune)
                    if not zone_ids:
                        logger.warning("bienici: aucune zone pour %s (%s)", commune.name, commune.insee)
                        continue
                    ads = await self._fetch_ads(client, criteria, zone_ids)
                    logger.info("bienici: %s -> %d annonces", commune.name, len(ads))
                    results.extend(ads)
                except httpx.HTTPError as exc:
                    logger.error("bienici: erreur réseau pour %s: %s", commune.insee, exc)
        return results

    async def _zone_ids(self, client: httpx.AsyncClient, commune: Commune) -> List[str]:
        resp = await client.get(SUGGEST_URL, params={"q": commune.name})
        resp.raise_for_status()
        suggestions = resp.json()

        zones: List[str] = []
        for sug in suggestions:
            if sug.get("type") != "city":
                continue
            insee_match = (
                commune.insee in (sug.get("insee_codes") or [])
                or sug.get("insee_code") == commune.insee
            )
            if insee_match:
                zones.extend(sug.get("zoneIds") or [])

        # Fallback: no exact INSEE match — take the first city suggestion
        if not zones:
            for sug in suggestions:
                if sug.get("type") == "city":
                    zones.extend(sug.get("zoneIds") or [])
                    break
        return zones

    def _bienici_types(self, criteria: SearchCriteria) -> List[str]:
        if not criteria.property_types:
            return DEFAULT_BIENICI_TYPES
        out = [PROPERTY_TYPE_TO_BIENICI[pt] for pt in criteria.property_types
               if pt in PROPERTY_TYPE_TO_BIENICI]
        return out or DEFAULT_BIENICI_TYPES

    async def _fetch_ads(
        self, client: httpx.AsyncClient, criteria: SearchCriteria, zone_ids: List[str]
    ) -> List[NormalizedListing]:
        listings: List[NormalizedListing] = []
        filter_type = "rent" if criteria.transaction_type == 1 else "buy"

        for page in range(1, MAX_PAGES + 1):
            filters = {
                "size": RESULTS_PER_PAGE,
                "from": (page - 1) * RESULTS_PER_PAGE,
                "filterType": filter_type,
                "propertyType": self._bienici_types(criteria),
                "page": page,
                "resultsPerPage": RESULTS_PER_PAGE,
                "maxAuthorizedResults": 2400,
                "sortBy": "publicationDate",
                "sortOrder": "desc",
                "onTheMarket": [True],
                "zoneIdsByTypes": {"zoneIds": zone_ids},
            }
            if criteria.budget_min is not None:
                filters["minPrice"] = criteria.budget_min
            if criteria.budget_max is not None:
                filters["maxPrice"] = criteria.budget_max
            if criteria.surface_min is not None:
                filters["minArea"] = criteria.surface_min
            if criteria.surface_max is not None:
                filters["maxArea"] = criteria.surface_max
            if criteria.room_min is not None:
                filters["minRooms"] = criteria.room_min
            if criteria.room_max is not None:
                filters["maxRooms"] = criteria.room_max
            if criteria.bedroom_min is not None:
                filters["minBedrooms"] = criteria.bedroom_min
            if criteria.bedroom_max is not None:
                filters["maxBedrooms"] = criteria.bedroom_max

            resp = await client.get(
                ADS_URL, params={"filters": json.dumps(filters, separators=(",", ":"))}
            )
            resp.raise_for_status()
            data = resp.json()

            ads = data.get("realEstateAds", [])
            for ad in ads:
                normalized = self._normalize(ad)
                if normalized:
                    listings.append(normalized)

            total = data.get("total", 0)
            if not ads or page * RESULTS_PER_PAGE >= total:
                break
        return listings

    def _normalize(self, ad: dict) -> Optional[NormalizedListing]:
        source_id = ad.get("id")
        if not source_id:
            return None

        blur = ad.get("blurInfo") or {}
        pos = blur.get("position") or blur.get("centroid") or {}
        district = ad.get("district") or {}
        photos = [p.get("url") for p in (ad.get("photos") or []) if p.get("url")]

        return NormalizedListing(
            source=self.source,
            source_id=str(source_id),
            title=ad.get("title"),
            description=ad.get("description"),
            price=_num(ad.get("price")),
            price_per_meter=_num(ad.get("pricePerSquareMeter")),
            surface=_num(ad.get("surfaceArea")),
            land_surface=_num(ad.get("landSurfaceArea")),
            room=ad.get("roomsQuantity"),
            bedroom=ad.get("bedroomsQuantity"),
            floor=ad.get("floor"),
            property_type=BIENICI_TO_PROPERTY_TYPE.get(ad.get("propertyType")),
            transaction_type=1 if ad.get("adType") == "rent" else 0,
            city_name=ad.get("city"),
            city_zipcode=ad.get("postalCode"),
            city_insee=district.get("insee_code") or district.get("code_insee"),
            department_code=ad.get("departmentCode"),
            latitude=pos.get("lat"),
            longitude=pos.get("lon"),
            energy_category=ad.get("energyClassification"),
            energy_value=ad.get("energyValue"),
            ghg_category=ad.get("greenhouseGazClassification"),
            ghg_value=ad.get("greenhouseGazValue"),
            agency=ad.get("accountDisplayName"),
            url=f"https://www.bienici.com/annonce/{source_id}",
            pictures=photos,
        )
