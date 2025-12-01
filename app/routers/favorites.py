from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.favorite import Favorite
from app.services.melo import MeloService
from app.services.enrichment import EnrichmentService
from app.schemas.favorite import FavoriteCreate, FavoriteResponse

router = APIRouter()
melo_service = MeloService()
enrichment_service = EnrichmentService()


@router.get("/", response_model=List[FavoriteResponse])
async def get_favorites(db: Session = Depends(get_db)):
    """Get all favorite properties."""
    favorites = db.query(Favorite).order_by(Favorite.created_at.desc()).all()
    return favorites


@router.post("/", response_model=FavoriteResponse)
async def add_favorite(data: FavoriteCreate, db: Session = Depends(get_db)):
    """Add a property to favorites and enrich it."""
    # Check if already exists
    existing = db.query(Favorite).filter(
        Favorite.property_uuid == data.property_uuid
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Property already in favorites")
    
    # Get full property data from Melo.io
    property_data = await melo_service.get_property(data.property_uuid)
    if not property_data:
        raise HTTPException(status_code=404, detail="Property not found")
    
    # Create favorite from property data
    favorite = Favorite(
        property_uuid=data.property_uuid,
        advert_uuid=property_data.get("lastCrawledAdvert"),
        title=property_data.get("title"),
        description=property_data.get("description"),
        price=property_data.get("price"),
        price_per_meter=property_data.get("pricePerMeter"),
        surface=property_data.get("surface"),
        land_surface=property_data.get("landSurface"),
        room=property_data.get("room"),
        bedroom=property_data.get("bedroom"),
        floor=property_data.get("floor"),
        property_type=property_data.get("propertyType"),
        transaction_type=property_data.get("transactionType"),
        pictures=property_data.get("pictures"),
        raw_data=property_data,
    )
    
    # Extract city info
    city = property_data.get("city", {})
    if city:
        favorite.city_name = city.get("name")
        favorite.city_zipcode = city.get("zipcode")
        favorite.city_insee = city.get("insee")
        dept = city.get("department", {})
        if dept:
            favorite.department_code = dept.get("code")
            favorite.department_name = dept.get("name")
        region = city.get("region", {})
        if region:
            favorite.region_name = region.get("name")
    
    # Extract location (API returns "location" not "locations")
    location = property_data.get("location") or {}
    if location:
        favorite.latitude = location.get("lat")
        favorite.longitude = location.get("lon")
    # Fallback to city location if property location not available
    if not favorite.latitude and city:
        city_location = city.get("location") or {}
        if city_location:
            favorite.latitude = city_location.get("lat")
            favorite.longitude = city_location.get("lon")
    
    # Extract advert info (contact, url, energy)
    adverts = property_data.get("adverts", [])
    if adverts:
        advert = adverts[0]
        contact = advert.get("contact", {})
        if contact:
            favorite.agency = contact.get("agency")
            favorite.contact_phone = contact.get("phone")
            favorite.contact_email = contact.get("email")
        favorite.url = advert.get("url")
        
        energy = advert.get("energy", {})
        if energy:
            favorite.energy_category = energy.get("category")
            favorite.energy_value = energy.get("value")
        
        ghg = advert.get("greenHouseGas", {})
        if ghg:
            favorite.ghg_category = ghg.get("category")
            favorite.ghg_value = ghg.get("value")
    
    # Enrich with zonage ABC and georisques
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
    
    # Re-fetch georisques
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
