from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import List, Optional

from curl_cffi.requests import AsyncSession
from selectolax.parser import HTMLParser

from app.config import get_settings
from app.scrapers.base import BaseScraper, SearchCriteria, NormalizedListing, Commune

logger = logging.getLogger("scrapers.paruvendu")

BASE = "https://www.paruvendu.fr"
# ParuVendu serves plain HTML with no anti-bot; curl_cffi (Chrome TLS) is used for
# parity with PAP and resilience against future UA gating. The all-types results
# page ``/immobilier/{vente|location}/{city}/`` returns every property type, so we
# hit one URL per commune and let the DB read filter by type/budget/surface.
IMPERSONATE = "chrome"

RESULTS_PER_PAGE = 30
MAX_PAGES = 10                  # refresh cap: ~300 ads / commune / run
MAX_PAGES_FIRST = 20            # first scrape backfills a bit deeper
REQUEST_DELAY = 1.0

DISTRIBUTION = {0: "vente", 1: "location"}

# Property-type label (from the card link's ``title``) -> our int. Matched on the
# first, accent-stripped word.
LABEL_TO_TYPE = {
    "appartement": 0, "studio": 0, "duplex": 0, "loft": 6,
    "maison": 1, "villa": 1, "propriete": 1, "chateau": 1, "mas": 1, "longere": 1,
    "parking": 2, "garage": 2, "box": 2,
    "terrain": 3,
    "local": 4, "bureau": 4, "bureaux": 4, "commerce": 4, "fonds": 4, "boutique": 4,
    "immeuble": 5,
}
TYPE_LABELS = {0: "Appartement", 1: "Maison", 2: "Parking", 3: "Terrain",
               4: "Local commercial", 5: "Immeuble", 6: "Loft"}

# A detail link ends in the alphanumeric ad id (digits + a letter suffix), which
# tells it apart from category/agency links that end in digits only.
_DETAIL_ID = re.compile(r"/(\d{6,}[A-Z0-9]{2,})/?$")
_TITLE = re.compile(
    r"^\s*([A-Za-zÀ-ÿ'’ -]+?)\s*-", re.UNICODE)
_ROOMS = re.compile(r"(\d+)\s*pi[eè]ce", re.IGNORECASE)
_SURFACE = re.compile(r"([\d.,]+)\s*m²")
_BEDROOMS = re.compile(r"(\d+)\s*chambre", re.IGNORECASE)
_DPE = re.compile(r"DPE\s*:?\s*([A-G])\b")
_PRICE = re.compile(r"([\d][\d\s. ]*)\s*€")


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _strip_accents(name).lower()).strip("-")
    return slug or "ville"


def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _dept(insee: Optional[str]) -> str:
    insee = (insee or "").zfill(5)
    return insee[:3] if insee[:2] in ("97", "98") else insee[:2]


class ParuVenduScraper(BaseScraper):
    """Scraper for paruvendu.fr — open HTML, no anti-bot.

    Flow: one all-types results page per commune (``/immobilier/{vente|location}/
    {city-slug}-{zipcode}/``, paginated with ``?p=N``), parsing each
    ``div.blocAnnonce`` card. Like PAP/SeLoger, cards carry no coordinates, so
    listings have no map marker and are tagged with the searched commune's INSEE
    (excluded from radius search). Budget/surface/type filtering is left to the DB
    read side.
    """
    source = "paruvendu"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        if not get_settings().paruvendu_enabled or not criteria.communes:
            return []
        results: List[NormalizedListing] = []
        async with AsyncSession() as session:
            for commune in criteria.communes:
                try:
                    ads = await self._fetch_commune(session, criteria, commune)
                    logger.info("paruvendu: %s -> %d annonces", commune.name, len(ads))
                    results.extend(ads)
                except Exception as exc:  # noqa: BLE001 — isolate each commune
                    logger.error("paruvendu: erreur pour %s: %s", commune.insee, exc)
        return results

    def _candidate_urls(self, criteria: SearchCriteria, commune: Commune) -> List[str]:
        """Both commune URL forms, unambiguous first. ParuVendu uses ``{slug}-{zip}``
        for most communes but a bare ``{slug}`` for big cities (the other 404s), so
        we try the zip form first (disambiguates same-named communes) then bare."""
        dist = DISTRIBUTION.get(criteria.transaction_type, "vente")
        slug = _slugify(commune.name)
        urls = []
        if commune.zipcode:
            urls.append(f"{BASE}/immobilier/{dist}/{slug}-{commune.zipcode}/")
        urls.append(f"{BASE}/immobilier/{dist}/{slug}/")
        return urls

    async def _get(self, session: AsyncSession, url: str):
        return await session.get(
            url, impersonate=IMPERSONATE, timeout=self.timeout, allow_redirects=True
        )

    async def _resolve(self, session, criteria, commune):
        """Return the (base_url, first_page_html) whose form ParuVendu serves."""
        for url in self._candidate_urls(criteria, commune):
            resp = await self._get(session, url)
            if resp.status_code == 200:
                return url, resp.content.decode("utf-8", "replace")
        return None, None

    async def _fetch_commune(
        self, session: AsyncSession, criteria: SearchCriteria, commune: Commune
    ) -> List[NormalizedListing]:
        base, html = await self._resolve(session, criteria, commune)
        if not base:
            logger.warning("paruvendu: commune introuvable %s", commune.name)
            return []

        listings: List[NormalizedListing] = []
        seen: set[str] = set()
        max_pages = MAX_PAGES_FIRST if criteria.first_scrape else MAX_PAGES

        for page in range(1, max_pages + 1):
            if page > 1:
                resp = await self._get(session, f"{base}?p={page}")
                if resp.status_code != 200:
                    break
                html = resp.content.decode("utf-8", "replace")  # UTF-8 despite header

            cards = [c for c in HTMLParser(html).css("div.blocAnnonce")
                     if (c.attributes.get("data-id") or "").isdigit()]
            if not cards:
                break
            page_new = 0
            for card in cards:
                item = self._normalize(card, criteria, commune)
                if item and item.source_id not in seen:
                    seen.add(item.source_id)
                    listings.append(item)
                    page_new += 1
            if page_new == 0:  # repeated last page — stop paginating
                break
            await asyncio.sleep(REQUEST_DELAY)
        return listings

    def _normalize(self, card, criteria: SearchCriteria,
                   commune: Commune) -> Optional[NormalizedListing]:
        # Scripts inside the card carry ids/timestamps — drop them before reading text.
        for script in card.css("script"):
            script.decompose()

        link = self._detail_link(card)
        if link is None:
            return None
        href = link.attributes.get("href") or ""
        m = _DETAIL_ID.search(href)
        if not m:
            return None
        source_id = m.group(1)

        title_attr = link.attributes.get("title") or ""
        property_type = self._type_from_title(title_attr)
        rooms = self._first_int(_ROOMS, title_attr)
        surface = self._first_float(_SURFACE, title_attr)

        price = self._price(card)
        text = re.sub(r"\s+", " ", card.text())
        if surface is None:
            surface = self._first_float(_SURFACE, text)
        bedrooms = self._first_int(_BEDROOMS, text)
        dpe = _DPE.search(text)

        return NormalizedListing(
            source=self.source,
            source_id=source_id,
            title=self._title(property_type, rooms, surface, commune.name),
            description=None,
            price=price,
            price_per_meter=round(price / surface, 2) if price and surface else None,
            surface=surface,
            room=rooms,
            bedroom=bedrooms,
            property_type=property_type,
            transaction_type=criteria.transaction_type,
            city_name=commune.name,
            city_zipcode=commune.zipcode,
            city_insee=commune.insee,  # normalized to parent by scrape._apply
            department_code=_dept(commune.insee),
            energy_category=dpe.group(1) if dpe else None,
            agency=self._agency(card),
            url=BASE + href if href.startswith("/") else href,
            pictures=self._pictures(card),
        )

    @staticmethod
    def _detail_link(card):
        for a in card.css("a"):
            if _DETAIL_ID.search(a.attributes.get("href") or ""):
                return a
        return None

    @staticmethod
    def _type_from_title(title: str) -> Optional[int]:
        m = _TITLE.match(title)
        if not m:
            return None
        first = _strip_accents(m.group(1)).lower().split()
        return LABEL_TO_TYPE.get(first[0]) if first else None

    @staticmethod
    def _first_int(rx: re.Pattern, text: str) -> Optional[int]:
        m = rx.search(text or "")
        return int(m.group(1)) if m else None

    @staticmethod
    def _first_float(rx: re.Pattern, text: str) -> Optional[float]:
        m = rx.search(text or "")
        return _to_float(m.group(1)) if m else None

    @staticmethod
    def _price(card) -> Optional[int]:
        """Price from the tight leaf ``<div>675 000 €</div>`` — the concatenated
        card text is polluted by an adjacent photo counter, so we read the div
        whose *direct* text holds the euro sign."""
        for d in card.css("div"):
            own = d.text(deep=False) or ""
            if "€" in own:
                m = _PRICE.search(own)
                if m:
                    digits = re.sub(r"[^\d]", "", m.group(1))
                    return int(digits) if digits else None
        return None

    @staticmethod
    def _pictures(card) -> List[str]:
        urls = []
        for img in card.css("img"):
            src = img.attributes.get("data-src") or img.attributes.get("src") or ""
            if "ubiflow" in src or "/media_ext/" in src:
                urls.append(src)
        return urls

    @staticmethod
    def _agency(card) -> Optional[str]:
        pro = card.css_first('a[href*="/immobilier/pro/"]')
        if pro is None:
            return "Particulier"
        img = pro.css_first("img")
        alt = (img.attributes.get("alt") if img else None) or pro.attributes.get("title") or ""
        alt = re.sub(r"^\s*(pro|part(?:iculier)?)\s*:\s*", "", alt, flags=re.I).strip()
        return alt or "Agence"

    def _title(self, property_type, rooms, surface, city) -> str:
        parts = [TYPE_LABELS.get(property_type, "Bien")]
        if rooms:
            parts.append("1 pièce" if rooms == 1 else f"{rooms} pièces")
        if surface:
            parts.append(f"{surface:g} m²")
        base = " ".join(parts)
        return f"{base} - {city}" if city else base
