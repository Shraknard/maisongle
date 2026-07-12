import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.listing import Listing, Hidden
from app.models.favorite import Favorite
from app.services.enrichment import EnrichmentService
from app.services.bien_dans_ma_ville import BienDansMaVilleService
from app.services.listing_view import serialize_listing, price_history

logger = logging.getLogger("routers.listings")
router = APIRouter()
enrichment_service = EnrichmentService()
bdmv_service = BienDansMaVilleService()


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

    # Commune-level socio-demographic stats (bien-dans-ma-ville.fr), cached per
    # INSEE. Identical for every listing in a city; non-blocking on failure.
    data["bien_dans_ma_ville"] = await bdmv_service.get_stats(
        db, listing.city_insee, listing.city_name
    )

    # Urban-heat-island (LCZ) overlay for the detail map. When the listing has no
    # coordinates (PAP/SeLoger/ParuVendu/notaires), fall back to the commune
    # centroid so the heat layer stays readable — the marker is then approximate.
    if (listing.latitude is None or listing.longitude is None) and listing.city_insee:
        # Lazy import: app.services.geo ↔ app.scrapers form an import cycle at
        # module load (same reason BienDansMaVilleService imports it lazily).
        from app.services.geo import get_commune, normalize_insee_code
        commune = await get_commune(normalize_insee_code(listing.city_insee))
        if commune and commune.latitude and commune.longitude:
            loc = {"lat": commune.latitude, "lon": commune.longitude, "approx": True}
            data["location"] = loc
            data["city"]["location"] = loc

    settings = get_settings()
    data["heat_overlay"] = (
        {
            "export_url": settings.heat_overlay_export_url,
            "layer": settings.heat_overlay_layer,
            "attribution": settings.heat_overlay_attribution,
        }
        if settings.heat_overlay_enabled and settings.heat_overlay_export_url
        else None
    )

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
