import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.listing import Listing, Hidden
from app.models.favorite import Favorite
from app.services.enrichment import EnrichmentService
from app.services.listing_view import serialize_listing, price_history

logger = logging.getLogger("routers.listings")
router = APIRouter()
enrichment_service = EnrichmentService()


def _split_uid(uuid: str):
    source, _, source_id = uuid.partition(":")
    return source, source_id


@router.get("/{uuid}")
async def get_listing(uuid: str, db: Session = Depends(get_db)):
    """Listing detail. Enrichment (zonage + georisques) comes from the favorite
    if it exists, otherwise it is computed on the fly for this view."""
    source, source_id = _split_uid(uuid)
    listing = db.query(Listing).filter_by(source=source, source_id=source_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    data = serialize_listing(listing)
    data["priceHistory"] = price_history(db, listing.id)

    favorite = db.query(Favorite).filter(Favorite.property_uuid == uuid).first()
    if favorite:
        data["zonage_abc"] = favorite.zonage_abc
        data["georisques"] = favorite.georisques
    else:
        enrichment = await enrichment_service.enrich_property(
            insee_code=listing.city_insee,
            latitude=listing.latitude,
            longitude=listing.longitude,
        )
        data["zonage_abc"] = enrichment.get("zonage_abc")
        data["georisques"] = enrichment.get("georisques")

    return data


@router.post("/{uuid}/hide")
def hide_listing(uuid: str, db: Session = Depends(get_db)):
    """Hide a listing for good: it is removed and never re-stored on re-scrape."""
    source, source_id = _split_uid(uuid)
    already = db.query(Hidden).filter_by(source=source, source_id=source_id).first()
    if not already:
        db.add(Hidden(source=source, source_id=source_id))
    db.query(Listing).filter_by(source=source, source_id=source_id).delete()
    db.commit()
    return {"message": "Listing hidden", "uuid": uuid}
