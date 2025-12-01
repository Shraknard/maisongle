from fastapi import APIRouter, HTTPException
from app.services.melo import MeloService

router = APIRouter()
melo_service = MeloService()


@router.get("/{property_uuid}")
async def get_property(property_uuid: str):
    """Get a single property from Melo.io API."""
    property_data = await melo_service.get_property(property_uuid)
    
    if not property_data:
        raise HTTPException(status_code=404, detail="Property not found")
    
    return property_data
