import logging
from fastapi import APIRouter, Query, Depends
from typing import Optional, List, Dict
from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DVFCommune, LoyerCommune, Listing
from app.scrapers.base import SearchCriteria
from app.services.geo import search_communes, get_commune, reverse_commune, normalize_insee_code
from app.services.scrape import refresh_if_stale
from app.services.listing_view import serialize_listing, price_drops

logger = logging.getLogger("routers.search")
router = APIRouter()

PAGE_SIZE = 50
_SORT_COLUMNS = {
    "price": Listing.price,
    "surface": Listing.surface,
    "date": Listing.first_seen,
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
    communes = []
    if city_id:
        for cid in city_id:
            insee = cid.split("/")[-1].strip()
            commune = await get_commune(insee)
            if commune:
                communes.append(commune)
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
    )

    # 2. Throttled on-demand scrape (only when we know which communes to scrape)
    refresh_info = {"scraped": False}
    if communes:
        try:
            refresh_info = await refresh_if_stale(db, criteria)
        except Exception:  # noqa: BLE001 — never let a scrape failure break search
            logger.exception("refresh a échoué, on sert le cache")

    # 3. Query the local DB
    insee_list = [normalize_insee_code(c.insee) for c in communes]
    if not insee_list and not department:
        return {"total": 0, "page": page, "properties": [], "refresh": refresh_info}

    query = db.query(Listing).filter(
        Listing.active.is_(True),
        Listing.transaction_type == transaction_type,
    )
    if insee_list:
        query = query.filter(Listing.city_insee.in_(insee_list))
    elif department:
        query = query.filter(Listing.department_code == department)

    if property_types:
        query = query.filter(Listing.property_type.in_(property_types))
    if budget_min is not None:
        query = query.filter(Listing.price >= budget_min)
    if budget_max is not None:
        query = query.filter(Listing.price <= budget_max)
    if surface_min is not None:
        query = query.filter(Listing.surface >= surface_min)
    if surface_max is not None:
        query = query.filter(Listing.surface <= surface_max)
    if room_min is not None:
        query = query.filter(Listing.room >= room_min)
    if room_max is not None:
        query = query.filter(Listing.room <= room_max)
    if bedroom_min is not None:
        query = query.filter(Listing.bedroom >= bedroom_min)
    if bedroom_max is not None:
        query = query.filter(Listing.bedroom <= bedroom_max)

    sort_col = _SORT_COLUMNS.get(sort_by, Listing.first_seen)
    direction = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    query = query.order_by(nullslast(direction))

    total = query.count()
    listings = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()

    drops = price_drops(db, [listing.id for listing in listings])
    properties = [serialize_listing(listing, drops.get(listing.id)) for listing in listings]

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
