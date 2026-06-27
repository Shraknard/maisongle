from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import List, Optional

from curl_cffi.requests import AsyncSession
from selectolax.parser import HTMLParser

from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune

logger = logging.getLogger("scrapers.pap")

BASE = "https://www.pap.fr"
AC_GEO_URL = f"{BASE}/json/ac-geo"
# PAP sits behind Cloudflare; a plain httpx call gets a JS challenge (403).
# curl_cffi replays a real Chrome TLS fingerprint, which clears the challenge.
IMPERSONATE = "chrome"

RESULTS_PER_PAGE = 15           # PAP serves ~15 ads / page
MAX_PAGES = 10                  # personal-use cap: ~150 ads / type / commune / run
REQUEST_DELAY = 1.0             # politeness pause between requests (seconds)

# Our int property type -> PAP URL type slug. Sale uses the plural form; rentals
# use the singular ``location-<type>`` form (the plural collapses to the combined,
# office-heavy ``locations-...`` listing). Only rental types with a working,
# unambiguous slug are listed — ``parking``/``terrain`` collapse to the combined
# listing and ``immeuble`` mis-resolves to ``meuble`` (furnished), so they are
# intentionally omitted for rentals.
SALE_TYPE_SLUG = {
    0: "appartements", 1: "maisons", 2: "parkings-box",
    3: "terrains", 4: "locaux-commerciaux", 5: "immeubles",
}
RENT_TYPE_SLUG = {
    0: "appartement", 1: "maison", 4: "local-commercial",
}
DEFAULT_TYPES = [0, 1]  # appartement + maison — the residential core

# PAP detail-URL slug -> our int property type. Checked longest-prefix first so
# ``local-commercial`` wins over ``local``.
PAP_SLUG_TO_TYPE = {
    "appartement": 0, "studio": 0, "duplex": 0, "chambre": 0, "colocation": 0,
    "maison": 1, "villa": 1, "propriete": 1, "chateau": 1, "manoir": 1, "moulin": 1,
    "parking": 2, "garage": 2, "box": 2,
    "terrain": 3,
    "local-commercial": 4, "local": 4, "bureau": 4, "bureaux": 4, "boutique": 4,
    "commerce": 4, "fonds-de-commerce": 4,
    "immeuble": 5,
    "loft": 6,
}
_SLUG_PREFIXES = sorted(PAP_SLUG_TO_TYPE, key=len, reverse=True)

TYPE_LABELS = {
    0: "Appartement", 1: "Maison", 2: "Parking",
    3: "Terrain", 4: "Local commercial", 5: "Immeuble", 6: "Loft",
}


def _norm(text: str) -> str:
    """Lowercase, strip accents and punctuation for name comparison."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _slugify(name: str) -> str:
    slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    return slug or "ville"


def _dept(insee: Optional[str]) -> str:
    insee = (insee or "").zfill(5)
    return insee[:3] if insee[:2] in ("97", "98") else insee[:2]


def _text(node, selector: str) -> Optional[str]:
    found = node.css_first(selector)
    return re.sub(r"\s+", " ", found.text()).strip() if found else None


def _parse_price(raw: Optional[str]) -> Optional[int]:
    """'279.000 €' / '1.710\xa0€/mois CC' -> int. Take the amount before the €."""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw.split("€")[0])
    return int(digits) if digits else None


def _parse_fr_number(raw: str) -> Optional[float]:
    """French-formatted number ('28,12', '1 200', '2.500,5') -> float."""
    s = re.sub(r"\s", "", raw.replace("\xa0", "").replace(" ", ""))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_city(h1: Optional[str]):
    """'Paris 20E (75020)' -> ('Paris 20E', '75020')."""
    if not h1:
        return None, None
    m = re.match(r"(.*?)\s*\((\d{5})\)\s*$", h1)
    if m:
        return m.group(1).strip(), m.group(2)
    return h1.strip(), None


def _parse_tags(card):
    """Read surface (m²) and room count from the ``item-tags`` list."""
    surface = rooms = None
    for li in card.css("ul.item-tags li"):
        txt = re.sub(r"\s+", " ", li.text()).strip()
        if "pièce" in txt:
            m = re.search(r"(\d+)", txt)
            if m:
                rooms = int(m.group(1))
        elif "m²" in txt or "m2" in txt:
            m = re.search(r"([\d\s.,]+)\s*m", txt)
            if m:
                surface = _parse_fr_number(m.group(1))
    return surface, rooms


def _parse_dpe(card) -> Optional[str]:
    """Energy class from the ``item-thumb-dpe-<letter>`` modifier class."""
    node = card.css_first("[class*='item-thumb-dpe-']")
    if not node:
        return None
    m = re.search(r"item-thumb-dpe-([a-g])\b", node.attributes.get("class", ""))
    return m.group(1).upper() if m else None


def _listing_href(card) -> Optional[str]:
    """The card's own listing link, e.g. ``/annonces/maison-lyon-3e-69003-r462801503``.

    Ignores interleaved promo/partner blocks (credit ads, construiresamaison
    affiliate land) that share the ``search-list-item-alt`` class but link off-site.
    """
    for a in card.css("a"):
        href = a.attributes.get("href") or ""
        if "/annonces/" in href and re.search(r"-r\d+", href):
            return href
    return None


def _ptype_from_href(href: str) -> Optional[int]:
    slug = href.split("/annonces/")[-1]
    # Some detail URLs carry an SEO transaction prefix (vente-/location-) before
    # the property type — drop it so the type token can match.
    slug = re.sub(r"^(?:vente|locations?|achat)-", "", slug)
    for prefix in _SLUG_PREFIXES:
        if slug.startswith(prefix + "-"):
            return PAP_SLUG_TO_TYPE[prefix]
    return None


class PapScraper(BaseScraper):
    """Scraper for pap.fr (Particulier à Particulier) — Cloudflare-fronted HTML.

    Flow: resolve each commune to a PAP ``g`` geo id via the ``ac-geo``
    autocomplete, then page through the result list (following the canonical
    "next" link) and parse each listing card. Price/surface/room filtering is
    left to the DB query in ``routers/search.py``; PAP's URL filter slugs are
    unreliable and get dropped on redirect.

    PAP list cards carry no coordinates, so listings have no map position; the
    searched commune's INSEE is attached for search matching.
    """
    source = "pap"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        results: List[NormalizedListing] = []
        async with AsyncSession() as session:
            for commune in criteria.communes:
                try:
                    geo_id = await self._geo_id(session, commune)
                    if not geo_id:
                        logger.warning("pap: aucune géo pour %s (%s)", commune.name, commune.insee)
                        continue
                    ads = await self._fetch_commune(session, criteria, commune, geo_id)
                    logger.info("pap: %s -> %d annonces", commune.name, len(ads))
                    results.extend(ads)
                except Exception as exc:  # noqa: BLE001 — isolate each commune
                    logger.error("pap: erreur pour %s: %s", commune.insee, exc)
        return results

    async def _geo_id(self, session: AsyncSession, commune: Commune) -> Optional[int]:
        resp = await session.get(
            AC_GEO_URL, params={"q": commune.name}, impersonate=IMPERSONATE, timeout=15
        )
        if resp.status_code != 200:
            return None
        suggestions = resp.json()  # [{"id": 43590, "name": "Lyon (69)"}, ...]
        if not suggestions:
            return None

        target = _norm(commune.name)
        dept = _dept(commune.insee)
        fallback = None
        for sug in suggestions:
            name = sug.get("name", "")
            m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
            base, paren = (m.group(1), m.group(2)) if m else (name, "")
            if _norm(base) != target:
                continue
            # The whole-commune entry tags its parens with the department code
            # (big cities) or the full postal code (smaller communes).
            if paren == dept or paren == (commune.zipcode or "") or paren[:2] == dept[:2]:
                return sug.get("id")
            fallback = fallback or sug.get("id")
        # No confident match — trust ac-geo's own ranking (best match first).
        return fallback or suggestions[0].get("id")

    def _type_slugs(self, criteria: SearchCriteria) -> List[str]:
        """PAP URL type slugs to crawl for this criteria, one request per slug."""
        table = RENT_TYPE_SLUG if criteria.transaction_type == 1 else SALE_TYPE_SLUG
        types = criteria.property_types or DEFAULT_TYPES
        slugs = [table[t] for t in types if t in table]
        if not slugs:  # requested types don't map (e.g. parking rental) — fall back
            slugs = [table[t] for t in DEFAULT_TYPES]
        return slugs

    def _start_url(self, criteria: SearchCriteria, commune: Commune,
                   geo_id: int, type_slug: str) -> str:
        city = _slugify(commune.name)
        dept = _dept(commune.insee)
        prefix = "location" if criteria.transaction_type == 1 else "vente"
        return f"{BASE}/annonce/{prefix}-{type_slug}-{city}-{dept}-g{geo_id}"

    async def _fetch_commune(
        self, session: AsyncSession, criteria: SearchCriteria,
        commune: Commune, geo_id: int,
    ) -> List[NormalizedListing]:
        listings: List[NormalizedListing] = []
        seen: set[str] = set()

        for type_slug in self._type_slugs(criteria):
            url: Optional[str] = self._start_url(criteria, commune, geo_id, type_slug)
            for _ in range(MAX_PAGES):
                resp = await session.get(url, impersonate=IMPERSONATE, timeout=self.timeout)
                if resp.status_code != 200:
                    logger.warning("pap: %s -> HTTP %s", url, resp.status_code)
                    break

                tree = HTMLParser(resp.text)
                cards = tree.css("div.search-list-item-alt")
                if not cards:
                    break
                for card in cards:
                    item = self._normalize(card, criteria, commune)
                    if item and item.source_id not in seen:
                        seen.add(item.source_id)
                        listings.append(item)

                # Follow PAP's own "next" link. Once it points at /proximite/ we
                # have exhausted this commune and entered neighbouring ones — stop.
                nxt = tree.css_first("#pagination-next")
                href = nxt.attributes.get("href") if nxt else None
                if not href or href.startswith("/proximite"):
                    break
                url = BASE + href
                await asyncio.sleep(REQUEST_DELAY)
        return listings

    def _normalize(self, card, criteria: SearchCriteria,
                   commune: Commune) -> Optional[NormalizedListing]:
        href = _listing_href(card)
        if not href:
            return None
        m = re.search(r"-r(\d+)", href)
        if not m:
            return None
        source_id = m.group(1)

        price = _parse_price(_text(card, "span.item-price"))
        surface, rooms = _parse_tags(card)
        city_name, zipcode = _parse_city(_text(card, "span.h1"))
        property_type = _ptype_from_href(href)
        pictures = [img.attributes.get("src")
                    for img in card.css(".owl-carousel img") if img.attributes.get("src")]

        return NormalizedListing(
            source=self.source,
            source_id=source_id,
            title=self._title(property_type, surface, rooms, city_name or commune.name),
            description=_text(card, "p.item-description"),
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            room=rooms,
            property_type=property_type,
            transaction_type=criteria.transaction_type,
            city_name=city_name or commune.name,
            city_zipcode=zipcode or commune.zipcode,
            city_insee=commune.insee,  # normalized to parent commune by scrape._apply
            department_code=_dept(commune.insee),
            energy_category=_parse_dpe(card),
            agency="Particulier",  # PAP = de particulier à particulier
            url=BASE + href,
            pictures=pictures,
        )

    def _title(self, property_type, surface, rooms, city) -> str:
        parts = [TYPE_LABELS.get(property_type, "Bien")]
        if rooms:
            parts.append("1 pièce" if rooms == 1 else f"{rooms} pièces")
        if surface:
            parts.append(f"{surface:g} m²")
        base = " ".join(parts)
        return f"{base} - {city}" if city else base
