import logging
import math
from fastapi import APIRouter, Query, Depends
from typing import Optional, List, Dict
from sqlalchemy import func, nullslast, select, cast, String
from sqlalchemy.orm import Session, Query as OrmQuery

from app.database import get_db
from app.models import DVFCommune, LoyerCommune, Listing
from app.scrapers.base import SearchCriteria
from app.services.geo import (
    search_communes,
    get_commune,
    reverse_commune,
    communes_within_radius,
    normalize_insee_code,
)
from app.services.scrape import refresh_if_stale
from app.services.listing_view import serialize_listing, price_drops

logger = logging.getLogger("routers.search")
router = APIRouter()

PAGE_SIZE = 50
EARTH_RADIUS_KM = 6371.0
_SORT_COLUMNS = {
    "price": Listing.price,
    "surface": Listing.surface,
    "date": Listing.first_seen,
}


def _within_radius(query, lat: float, lon: float, radius_km: float):
    """Restrict a Listing query to those within ``radius_km`` of a point.

    A cheap lat/lon bounding box prefilters rows, then an exact great-circle
    (haversine) distance computed in SQL trims to the precise circle. Listings
    without coordinates (e.g. PAP) are excluded — radius search is Bien'ici-only.
    """
    dlat = radius_km / 111.0
    cos_lat = math.cos(math.radians(lat)) or 1e-9
    dlon = radius_km / (111.0 * cos_lat)
    query = query.filter(
        Listing.latitude.isnot(None),
        Listing.longitude.isnot(None),
        Listing.latitude.between(lat - dlat, lat + dlat),
        Listing.longitude.between(lon - dlon, lon + dlon),
    )
    sin_dlat = func.sin(func.radians(Listing.latitude - lat) / 2)
    sin_dlon = func.sin(func.radians(Listing.longitude - lon) / 2)
    a = (
        sin_dlat * sin_dlat
        + func.cos(func.radians(lat))
        * func.cos(func.radians(Listing.latitude))
        * sin_dlon * sin_dlon
    )
    distance = 2 * EARTH_RADIUS_KM * func.asin(func.sqrt(a))
    return query.filter(distance <= radius_km)


def _representative_ids(base: OrmQuery):
    """One listing id per dedup cluster from the filtered ``base`` query.

    Uses ROW_NUMBER over the dedup_key, coalescing a null key with the row id so
    coordinate-less rows that couldn't be fingerprinted are never merged together.
    The representative is the row with coordinates (for the map), then the cheapest.
    """
    partition = func.coalesce(Listing.dedup_key, cast(Listing.id, String))
    rank = func.row_number().over(
        partition_by=partition,
        order_by=(
            Listing.latitude.is_(None),          # coordinates first (False < True)
            nullslast(Listing.price.asc()),      # cheapest as the representative
            Listing.id.asc(),
        ),
    ).label("rn")
    ranked = base.with_entities(Listing.id.label("id"), rank).subquery()
    return select(ranked.c.id).where(ranked.c.rn == 1)


def _duplicate_sources(base: OrmQuery, listings: list) -> Dict[str, dict]:
    """For each shown listing, the set of sources carrying the same property.

    Aggregates over the *pre-dedup* filtered set (so it sees every source), keyed
    by dedup_key and restricted to the current page's keys — a light query that
    lets the UI show "aussi sur Leboncoin, SeLoger" and the lowest price.
    """
    keys = [l.dedup_key for l in listings if l.dedup_key]
    if not keys:
        return {}
    rows = (
        base.with_entities(Listing.dedup_key, Listing.source, Listing.price)
        .filter(Listing.dedup_key.in_(keys))
        .all()
    )
    agg: Dict[str, dict] = {}
    for key, source, price in rows:
        entry = agg.setdefault(key, {"sources": set(), "min_price": None})
        entry["sources"].add(source)
        if price is not None and (entry["min_price"] is None or price < entry["min_price"]):
            entry["min_price"] = price
    return {
        key: {
            "sources": sorted(entry["sources"]),
            "count": len(entry["sources"]),
            "minPrice": entry["min_price"],
        }
        for key, entry in agg.items()
        if len(entry["sources"]) > 1
    }


@router.get("/locations")
async def search_locations(q: str = Query(..., min_length=2)):
    """City/postal-code autocomplete via the official geo.api.gouv.fr API."""
    return await search_communes(q)


@router.get("/")
async def search_listings(
    transaction_type: int = 0,
    department: Optional[str] = None,
    budget_min: Optional[int] = None,
    budget_max: Optional[int] = None,
    surface_min: Optional[int] = None,
    surface_max: Optional[int] = None,
    room_min: Optional[int] = None,
    room_max: Optional[int] = None,
    bedroom_min: Optional[int] = None,
    bedroom_max: Optional[int] = None,
    city_id: Optional[List[str]] = Query(None),
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    radius: Optional[int] = None,
    property_types: Optional[List[int]] = Query(None),
    page: int = 1,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Search locally stored listings, triggering a throttled scrape first.

    The frontend sends INSEE codes as ``city_id`` (from the autocomplete). A
    radius search sends ``lat``/``lon``; we reverse-geocode it to a commune.
    """
    # 1. Resolve target communes
    is_radius = radius is not None and lat is not None and lon is not None
    communes = []
    if city_id:
        for cid in city_id:
            insee = cid.split("/")[-1].strip()
            commune = await get_commune(insee)
            if commune:
                communes.append(commune)
    elif is_radius:
        communes = await communes_within_radius(lat, lon, float(radius))
    elif lat is not None and lon is not None:
        commune = await reverse_commune(lat, lon)
        if commune:
            communes.append(commune)

    criteria = SearchCriteria(
        communes=communes,
        transaction_type=transaction_type,
        property_types=property_types or [],
        budget_min=budget_min,
        budget_max=budget_max,
        surface_min=surface_min,
        surface_max=surface_max,
        room_min=room_min,
        room_max=room_max,
        bedroom_min=bedroom_min,
        bedroom_max=bedroom_max,
        center_lat=lat if is_radius else None,
        center_lon=lon if is_radius else None,
        radius_km=float(radius) if is_radius else None,
    )

    # 2. Throttled on-demand scrape (only when we know which communes to scrape).
    #    Radius search has no coordinates on PAP listings, so it scrapes Bien'ici only.
    refresh_info = {"scraped": False}
    if communes:
        try:
            # Radius search needs coordinates: Bien'ici and Leboncoin carry them,
            # PAP does not, so it is excluded from radius runs.
            sources = ["bienici", "leboncoin"] if is_radius else None
            refresh_info = await refresh_if_stale(db, criteria, sources=sources)
        except Exception:  # noqa: BLE001 — never let a scrape failure break search
            logger.exception("refresh a échoué, on sert le cache")
            db.rollback()  # clear any half-applied transaction before the read

    # 3. Query the local DB
    insee_list = [normalize_insee_code(c.insee) for c in communes]
    if not is_radius and not insee_list and not department:
        return {"total": 0, "page": page, "properties": [], "refresh": refresh_info}

    base = db.query(Listing).filter(
        Listing.active.is_(True),
        Listing.transaction_type == transaction_type,
    )
    if is_radius:
        base = _within_radius(base, lat, lon, float(radius))
    elif insee_list:
        base = base.filter(Listing.city_insee.in_(insee_list))
    elif department:
        base = base.filter(Listing.department_code == department)

    if property_types:
        base = base.filter(Listing.property_type.in_(property_types))
    if budget_min is not None:
        base = base.filter(Listing.price >= budget_min)
    if budget_max is not None:
        base = base.filter(Listing.price <= budget_max)
    if surface_min is not None:
        base = base.filter(Listing.surface >= surface_min)
    if surface_max is not None:
        base = base.filter(Listing.surface <= surface_max)
    if room_min is not None:
        base = base.filter(Listing.room >= room_min)
    if room_max is not None:
        base = base.filter(Listing.room <= room_max)
    if bedroom_min is not None:
        base = base.filter(Listing.bedroom >= bedroom_min)
    if bedroom_max is not None:
        base = base.filter(Listing.bedroom <= bedroom_max)

    # Collapse the same property listed on several sources: keep one
    # representative per dedup cluster (prefer one carrying coordinates, then the
    # cheapest), while rows without a dedup_key are each their own cluster.
    query = db.query(Listing).filter(Listing.id.in_(_representative_ids(base)))

    sort_col = _SORT_COLUMNS.get(sort_by, Listing.first_seen)
    direction = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    query = query.order_by(nullslast(direction))

    total = query.count()
    listings = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    drops = price_drops(db, [listing.id for listing in listings])
    dups = _duplicate_sources(base, listings)
    properties = [
        serialize_listing(listing, drops.get(listing.id), dups.get(listing.dedup_key))
        for listing in listings
    ]

    return {"total": total, "page": page, "properties": properties, "refresh": refresh_info}


@router.get("/dvf/{insee_code}")
def get_dvf_data(insee_code: str, db: Session = Depends(get_db)):
    """Get DVF (price) data for a commune by INSEE code."""
    normalized_code = normalize_insee_code(insee_code)
    dvf = db.query(DVFCommune).filter(DVFCommune.insee_com == normalized_code).first()
    if dvf:
        return {
            "insee_com": dvf.insee_com,
            "annee": dvf.annee,
            "nb_mutations": dvf.nb_mutations,
            "nb_maisons": dvf.nb_maisons,
            "nb_apparts": dvf.nb_apparts,
            "prop_maison": dvf.prop_maison,
            "prop_appart": dvf.prop_appart,
            "prix_moyen": dvf.prix_moyen,
            "prix_m2_moyen": dvf.prix_m2_moyen,
            "surface_moy": dvf.surface_moy,
        }
    return None


@router.post("/dvf/batch")
def get_dvf_batch(insee_codes: List[str], db: Session = Depends(get_db)) -> Dict[str, dict]:
    """Get DVF data for multiple communes at once."""
    code_mapping = {}
    for code in insee_codes:
        original = code.zfill(5)
        normalized = normalize_insee_code(code)
        code_mapping[original] = normalized

    unique_codes = list(set(code_mapping.values()))
    dvfs = db.query(DVFCommune).filter(DVFCommune.insee_com.in_(unique_codes)).all()

    dvf_lookup = {}
    for dvf in dvfs:
        dvf_lookup[dvf.insee_com] = {
            "prix_m2_moyen": dvf.prix_m2_moyen,
            "prix_moyen": dvf.prix_moyen,
        }

    result = {}
    for original, normalized in code_mapping.items():
        if normalized in dvf_lookup:
            result[original] = dvf_lookup[normalized]

    return result


@router.get("/loyers/{insee_code}")
def get_loyers_data(
    insee_code: str,
    type_bien: str = Query("appartement", description="Type: appartement, app_t1t2, app_t3plus, maison"),
    db: Session = Depends(get_db)
):
    """Get rental price data for a commune by INSEE code."""
    code = insee_code.zfill(5)
    loyer = db.query(LoyerCommune).filter(
        LoyerCommune.insee_com == code,
        LoyerCommune.type_bien == type_bien
    ).first()

    if not loyer:
        if code == '75056':
            loyers = db.query(LoyerCommune).filter(
                LoyerCommune.insee_com.like('751%'),
                LoyerCommune.type_bien == type_bien
            ).all()
            if loyers:
                avg_loyer = sum(l.loyer_m2 for l in loyers if l.loyer_m2) / len([l for l in loyers if l.loyer_m2])
                return {
                    "insee_com": code,
                    "type_bien": type_bien,
                    "loyer_m2": avg_loyer,
                    "type_pred": "moyenne_arrondissements",
                    "nb_obs_commune": sum(l.nb_obs_commune or 0 for l in loyers),
                    "fiable": True,
                }
        elif code == '69123':
            loyers = db.query(LoyerCommune).filter(
                LoyerCommune.insee_com.like('6938%'),
                LoyerCommune.type_bien == type_bien
            ).all()
            if loyers:
                avg_loyer = sum(l.loyer_m2 for l in loyers if l.loyer_m2) / len([l for l in loyers if l.loyer_m2])
                return {
                    "insee_com": code,
                    "type_bien": type_bien,
                    "loyer_m2": avg_loyer,
                    "type_pred": "moyenne_arrondissements",
                    "nb_obs_commune": sum(l.nb_obs_commune or 0 for l in loyers),
                    "fiable": True,
                }
        elif code == '13055':
            loyers = db.query(LoyerCommune).filter(
                LoyerCommune.insee_com.like('132%'),
                LoyerCommune.insee_com >= '13201',
                LoyerCommune.insee_com <= '13216',
                LoyerCommune.type_bien == type_bien
            ).all()
            if loyers:
                avg_loyer = sum(l.loyer_m2 for l in loyers if l.loyer_m2) / len([l for l in loyers if l.loyer_m2])
                return {
                    "insee_com": code,
                    "type_bien": type_bien,
                    "loyer_m2": avg_loyer,
                    "type_pred": "moyenne_arrondissements",
                    "nb_obs_commune": sum(l.nb_obs_commune or 0 for l in loyers),
                    "fiable": True,
                }

    if loyer:
        return {
            "insee_com": loyer.insee_com,
            "type_bien": loyer.type_bien,
            "loyer_m2": loyer.loyer_m2,
            "loyer_m2_min": loyer.loyer_m2_min,
            "loyer_m2_max": loyer.loyer_m2_max,
            "type_pred": loyer.type_pred,
            "nb_obs_commune": loyer.nb_obs_commune,
            "fiable": loyer.nb_obs_commune >= 30 and loyer.r2_adj >= 0.5 if loyer.r2_adj else False,
        }
    return None


@router.post("/loyers/batch")
def get_loyers_batch(
    insee_codes: List[str],
    type_bien: str = Query("appartement", description="Type: appartement, app_t1t2, app_t3plus, maison"),
    db: Session = Depends(get_db)
) -> Dict[str, dict]:
    """Get rental price data for multiple communes at once."""
    code_mapping = {}
    for code in insee_codes:
        original = code.zfill(5)
        normalized = normalize_insee_code(code)
        code_mapping[original] = normalized

    unique_codes = list(set(code_mapping.values()))
    loyers = db.query(LoyerCommune).filter(
        LoyerCommune.insee_com.in_(unique_codes),
        LoyerCommune.type_bien == type_bien
    ).all()

    loyer_lookup = {}
    for loyer in loyers:
        loyer_lookup[loyer.insee_com] = {
            "loyer_m2": loyer.loyer_m2,
            "loyer_m2_min": loyer.loyer_m2_min,
            "loyer_m2_max": loyer.loyer_m2_max,
        }

    result = {}
    for original, normalized in code_mapping.items():
        if normalized in loyer_lookup:
            result[original] = loyer_lookup[normalized]

    return result
