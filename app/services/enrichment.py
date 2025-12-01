from typing import Optional, Dict, Any
from app.services.georisques import GeorisquesService
from app.services.zonage import ZonageService
from app.database import SessionLocal


class EnrichmentService:
    """Service for enriching property data with external sources."""
    
    def __init__(self):
        self.georisques_service = GeorisquesService()
    
    async def enrich_property(
        self,
        insee_code: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Enrich property with zonage ABC and georisques data."""
        
        result = {
            "zonage_abc": None,
            "georisques": None,
        }
        
        # Get zonage ABC
        if insee_code:
            db = SessionLocal()
            try:
                zonage_service = ZonageService(db)
                result["zonage_abc"] = zonage_service.get_zonage_by_insee(insee_code)
            finally:
                db.close()
        
        # Get georisques
        georisques = await self.georisques_service.get_all_risks(
            latitude=latitude,
            longitude=longitude,
            code_insee=insee_code
        )
        result["georisques"] = georisques
        
        return result
