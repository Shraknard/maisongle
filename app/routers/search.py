from fastapi import APIRouter, Query, Depends
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from app.services.melo import MeloService
from app.database import get_db
from app.models import DVFCommune, LoyerCommune

router = APIRouter()
melo_service = MeloService()


@router.get("/locations")
async def search_locations(q: str = Query(..., min_length=2, description="Search query for city name or zipcode")):
    """Search for cities/locations by name or zipcode (autocomplete)."""
    locations = await melo_service.search_locations(q)
    return locations


@router.get("/")
async def search_properties(
    # Location filters
    department: Optional[str] = Query(None, description="Department code (e.g., 77)"),
    city_id: Optional[List[str]] = Query(None, description="City ID(s) from autocomplete (e.g., /cities/30950)"),
    city_insee: Optional[str] = Query(None, description="City INSEE code"),
    zipcode: Optional[str] = Query(None, description="Zipcode"),
    lat: Optional[float] = Query(None, description="Latitude for radius search"),
    lon: Optional[float] = Query(None, description="Longitude for radius search"),
    radius: Optional[int] = Query(None, description="Radius in km (requires lat/lon)"),
    
    # Property filters
    property_types: Optional[List[int]] = Query(None, description="List of property types: 0=apartment, 1=house, 2=parking, 3=land, 4=shop, 5=building, 6=loft"),
    transaction_type: Optional[int] = Query(0, description="0=sale, 1=rent"),
    
    # Price filters
    budget_min: Optional[int] = Query(None, description="Minimum price"),
    budget_max: Optional[int] = Query(None, description="Maximum price"),
    
    # Surface filters
    surface_min: Optional[int] = Query(None, description="Minimum surface (m²)"),
    surface_max: Optional[int] = Query(None, description="Maximum surface (m²)"),
    
    # Room filters
    room_min: Optional[int] = Query(None, description="Minimum rooms"),
    room_max: Optional[int] = Query(None, description="Maximum rooms"),
    bedroom_min: Optional[int] = Query(None, description="Minimum bedrooms"),
    bedroom_max: Optional[int] = Query(None, description="Maximum bedrooms"),
    
    # Sorting
    sort_by: Optional[str] = Query(None, description="Sort field: price, surface, date"),
    sort_order: Optional[str] = Query(None, description="Sort order: asc, desc"),
    
    # Pagination
    page: int = Query(1, ge=1, description="Page number"),
):
    """Search properties via Melo.io API."""
    results = await melo_service.search_properties(
        department=department,
        city_id=city_id,
        city_insee=city_insee,
        zipcode=zipcode,
        lat=lat,
        lon=lon,
        radius=radius,
        property_types=property_types,
        transaction_type=transaction_type,
        budget_min=budget_min,
        budget_max=budget_max,
        surface_min=surface_min,
        surface_max=surface_max,
        room_min=room_min,
        room_max=room_max,
        bedroom_min=bedroom_min,
        bedroom_max=bedroom_max,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
    )
    return results


def normalize_insee_code(code: str) -> str:
    """Normalize INSEE code - map Paris/Lyon/Marseille arrondissements to main city code."""
    code = code.zfill(5)
    # Paris arrondissements (75101-75120) -> 75056
    if code.startswith('751') and len(code) == 5:
        return '75056'
    # Lyon arrondissements (69381-69389) -> 69123
    if code.startswith('6938') and len(code) == 5:
        return '69123'
    # Marseille arrondissements (13201-13216) -> 13055
    if code.startswith('132') and len(code) == 5 and code >= '13201' and code <= '13216':
        return '13055'
    return code


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
    # Normalize codes and keep mapping
    code_mapping = {}  # original -> normalized
    for code in insee_codes:
        original = code.zfill(5)
        normalized = normalize_insee_code(code)
        code_mapping[original] = normalized
    
    # Get unique normalized codes
    unique_codes = list(set(code_mapping.values()))
    dvfs = db.query(DVFCommune).filter(DVFCommune.insee_com.in_(unique_codes)).all()
    
    # Build lookup by normalized code
    dvf_lookup = {}
    for dvf in dvfs:
        dvf_lookup[dvf.insee_com] = {
            "prix_m2_moyen": dvf.prix_m2_moyen,
            "prix_moyen": dvf.prix_moyen,
        }
    
    # Return results mapped to original codes
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
    # For loyers, data is available by arrondissement, so try exact code first
    code = insee_code.zfill(5)
    loyer = db.query(LoyerCommune).filter(
        LoyerCommune.insee_com == code,
        LoyerCommune.type_bien == type_bien
    ).first()
    
    # If not found and it's a main city code, try to get average of arrondissements
    if not loyer:
        if code == '75056':  # Paris
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
        elif code == '69123':  # Lyon
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
        elif code == '13055':  # Marseille
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
    # Normalize codes and keep mapping
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
