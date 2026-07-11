from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

import httpx

from app.config import get_settings
from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune
from app.services.geo import normalize_insee_code

logger = logging.getLogger("scrapers.notaires")

# Open JSON API behind the immobilier.notaires.fr AngularJS SPA (no anti-bot).
# The search endpoint filters by ``departement`` (a commune-level ``localites``
# filter exists but needs an internal hexavia id we don't have). Notary volumes
# are small (a whole department is a few hundred ads), so we page the department
# and keep the ads whose real INSEE matches the searched communes — client-side,
# like the DB read filter. typeTransaction/typeBien URL params are ignored by the
# API, so those are filtered on our side too, from each ad's own fields.
SEARCH_URL = "https://www.immobilier.notaires.fr/pub-services/inotr-www-annonces/v1/annonces"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

PER_PAGE = 100
MAX_PAGES = 25                  # bound a department fetch (~2500 ads, ample here)
REQUEST_DELAY = 0.5             # politeness pause between pages

# All transaction types the API groups under a query (sale variants + rent).
ALL_TRANSACTIONS = "VENTE,VNI,VAE,LOCATION,VAT"

# Notary ``typeBien`` enum -> our int property type.
TYPE_TO_INT = {
    "APP": 0,                   # appartement
    "MAI": 1,                   # maison
    "GAR": 2,                   # garage / parking
    "TER": 3,                   # terrain
    "LAC": 4, "LOC": 4, "FON": 4, "BUR": 4,   # local / commerce / bureau
    "IMM": 5,                   # immeuble
    # "DIV" (divers) and unknowns -> None
}
# Sale-side transactions (everything that isn't a rental).
RENT_TRANSACTIONS = {"LOCATION"}


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


def _dept(insee: Optional[str]) -> str:
    insee = (insee or "").zfill(5)
    return insee[:3] if insee[:2] in ("97", "98") else insee[:2]


class NotairesScraper(BaseScraper):
    """Scraper for immobilier.notaires.fr — open JSON API, no anti-bot.

    Notary sales (and auctions / immo-interactif) are inventory that rarely
    reaches the portals, so this is genuinely additive. Flow: group the searched
    communes by department, page each department's ads once, and keep those whose
    INSEE matches a searched commune and whose transaction matches the search.

    Like PAP/SeLoger, notary cards carry no coordinates, so listings have no map
    marker and are excluded from radius search; the ad's own INSEE is stored.
    """
    source = "notaires"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.immobilier.notaires.fr/fr/annonces-immobilieres",
        }

    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        s = get_settings()
        if not s.notaires_enabled or not criteria.communes:
            return []

        # Which normalized INSEE codes are we keeping, grouped by department.
        wanted: set[str] = {normalize_insee_code(c.insee) for c in criteria.communes}
        depts: Dict[str, Commune] = {}
        for commune in criteria.communes:
            depts.setdefault(_dept(commune.insee), commune)

        results: List[NormalizedListing] = []
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            for dept in depts:
                try:
                    ads = await self._fetch_department(client, criteria, dept, wanted)
                except httpx.HTTPError as exc:
                    logger.error("notaires: erreur réseau dept %s: %s", dept, exc)
                    continue
                logger.info("notaires: dept %s -> %d annonces", dept, len(ads))
                results.extend(ads)
        return results

    async def _fetch_department(
        self, client: httpx.AsyncClient, criteria: SearchCriteria,
        dept: str, wanted: set,
    ) -> List[NormalizedListing]:
        listings: List[NormalizedListing] = []
        seen: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            params = {
                "typeTransaction": ALL_TRANSACTIONS,
                "departement": dept,
                "page": page,
                "parPage": PER_PAGE,
            }
            resp = await client.get(SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            ads = data.get("annonceResumeDto") or []
            if not ads:
                break
            for ad in ads:
                item = self._normalize(ad, criteria, wanted)
                if item and item.source_id not in seen:
                    seen.add(item.source_id)
                    listings.append(item)

            if page >= (data.get("nbPages") or 1):
                break
            await asyncio.sleep(REQUEST_DELAY)
        return listings

    def _normalize(self, ad: dict, criteria: SearchCriteria,
                   wanted: set) -> Optional[NormalizedListing]:
        insee = ad.get("inseeCommune")
        if not insee or normalize_insee_code(insee) not in wanted:
            return None

        # Keep only ads matching the searched transaction (the API returns all).
        is_rent = ad.get("typeTransaction") in RENT_TRANSACTIONS
        if (1 if is_rent else 0) != criteria.transaction_type:
            return None

        annonce_id = ad.get("annonceId") or ad.get("id")
        if not annonce_id:
            return None

        price = _to_float(ad.get("prixAffiche")) or _to_float(ad.get("prixTotal"))
        surface = _to_float(ad.get("surface"))
        photo = ad.get("urlPhotoPrincipale")

        return NormalizedListing(
            source=self.source,
            source_id=str(annonce_id),
            title=self._title(ad),
            description=ad.get("descriptionFr"),
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            land_surface=_to_float(ad.get("surfaceTerrain")),
            room=_to_int(ad.get("nbPieces")),
            bedroom=_to_int(ad.get("nbChambres")),
            property_type=TYPE_TO_INT.get(ad.get("typeBien")),
            transaction_type=1 if is_rent else 0,
            city_name=ad.get("communeNom"),
            city_zipcode=ad.get("codePostal"),
            city_insee=insee,  # real INSEE; normalized to parent by scrape._apply
            department_code=ad.get("inseeDepartement"),
            # No coordinates on notary cards -> no map marker / no radius.
            agency="Notaire",
            url=ad.get("urlDetailAnnonceFr"),
            pictures=[photo] if photo else [],
        )

    @staticmethod
    def _title(ad: dict) -> Optional[str]:
        bien = {0: "Appartement", 1: "Maison", 2: "Garage", 3: "Terrain",
                4: "Local", 5: "Immeuble"}.get(TYPE_TO_INT.get(ad.get("typeBien")), "Bien")
        parts = [bien]
        rooms = _to_int(ad.get("nbPieces"))
        if rooms:
            parts.append("1 pièce" if rooms == 1 else f"{rooms} pièces")
        surface = _to_float(ad.get("surface"))
        if surface:
            parts.append(f"{surface:g} m²")
        city = ad.get("communeNom")
        base = " ".join(parts)
        return f"{base} - {city}" if city else base
