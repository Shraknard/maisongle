from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.favorite import Favorite
from app.models.listing import Listing
from app.services.enrichment import EnrichmentService
from app.schemas.favorite import FavoriteCreate, FavoriteResponse

router = APIRouter()
enrichment_service = EnrichmentService()

# Fields copied into a Favorite snapshot, sourced from the listing when available.
_SNAPSHOT_FIELDS = (
    "title", "description", "price", "price_per_meter", "surface", "land_surface",
    "room", "bedroom", "floor", "property_type", "transaction_type", "pictures",
    "city_name", "city_zipcode", "city_insee", "department_code", "department_name",
    "region_name", "latitude", "longitude", "energy_category", "energy_value",
    "ghg_category", "ghg_value", "agency", "contact_phone", "contact_email", "url",
)


@router.get("/", response_model=List[FavoriteResponse])
async def get_favorites(db: Session = Depends(get_db)):
    """Get all favorite properties."""
    favorites = db.query(Favorite).order_by(Favorite.created_at.desc()).all()
    return favorites


@router.post("/", response_model=FavoriteResponse)
async def add_favorite(data: FavoriteCreate, db: Session = Depends(get_db)):
    """Add a property to favorites and enrich it."""
    existing = db.query(Favorite).filter(
        Favorite.property_uuid == data.property_uuid
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Property already in favorites")

    # The frontend only sends the uuid; copy the full snapshot from the stored
    # listing. Fall back to any fields provided directly in the request body.
    source_obj = None
    if ":" in data.property_uuid:
        source, _, source_id = data.property_uuid.partition(":")
        source_obj = db.query(Listing).filter_by(source=source, source_id=source_id).first()
    if source_obj is None:
        source_obj = data

    favorite = Favorite(property_uuid=data.property_uuid)
    for field in _SNAPSHOT_FIELDS:
        setattr(favorite, field, getattr(source_obj, field, None))

    enrichment = await enrichment_service.enrich_property(
        insee_code=favorite.city_insee,
        latitude=favorite.latitude,
        longitude=favorite.longitude
    )
    favorite.zonage_abc = enrichment.get("zonage_abc")
    favorite.georisques = enrichment.get("georisques")

    db.add(favorite)
    db.commit()
    db.refresh(favorite)

    return favorite


@router.delete("/{property_uuid}")
async def remove_favorite(property_uuid: str, db: Session = Depends(get_db)):
    """Remove a property from favorites."""
    favorite = db.query(Favorite).filter(
        Favorite.property_uuid == property_uuid
    ).first()

    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")

    db.delete(favorite)
    db.commit()

    return {"message": "Favorite removed successfully"}


@router.get("/{property_uuid}", response_model=FavoriteResponse)
async def get_favorite(property_uuid: str, db: Session = Depends(get_db)):
    """Get a specific favorite property."""
    favorite = db.query(Favorite).filter(
        Favorite.property_uuid == property_uuid
    ).first()

    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")

    return favorite


@router.post("/{property_uuid}/refresh-georisques", response_model=FavoriteResponse)
async def refresh_georisques(property_uuid: str, db: Session = Depends(get_db)):
    """Refresh georisques data for a favorite property."""
    favorite = db.query(Favorite).filter(
        Favorite.property_uuid == property_uuid
    ).first()

    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")

    enrichment = await enrichment_service.enrich_property(
        insee_code=favorite.city_insee,
        latitude=favorite.latitude,
        longitude=favorite.longitude
    )

    favorite.georisques = enrichment.get("georisques")
    if not favorite.zonage_abc:
        favorite.zonage_abc = enrichment.get("zonage_abc")

    db.commit()
    db.refresh(favorite)

    return favorite


@router.post("/refresh-all-georisques")
async def refresh_all_georisques(db: Session = Depends(get_db)):
    """Refresh georisques data for all favorites."""
    favorites = db.query(Favorite).all()
    updated = 0

    for favorite in favorites:
        try:
            enrichment = await enrichment_service.enrich_property(
                insee_code=favorite.city_insee,
                latitude=favorite.latitude,
                longitude=favorite.longitude
            )

            favorite.georisques = enrichment.get("georisques")
            if not favorite.zonage_abc:
                favorite.zonage_abc = enrichment.get("zonage_abc")
            updated += 1
        except Exception as e:
            print(f"Error refreshing georisques for {favorite.property_uuid}: {e}")

    db.commit()

    return {"message": f"Refreshed georisques for {updated}/{len(favorites)} favorites"}
