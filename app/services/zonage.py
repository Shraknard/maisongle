from typing import Optional
from sqlalchemy.orm import Session
from app.models.zonage import ZonageCommune


class ZonageService:
    """Service for zonage ABC lookup."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_zonage_by_insee(self, code_insee: str) -> Optional[str]:
        """Get zonage ABC for a commune by INSEE code."""
        zonage = self.db.query(ZonageCommune).filter(
            ZonageCommune.code_insee == code_insee
        ).first()
        
        if zonage:
            return zonage.zonage
        return None
    
    def get_zonage_by_zipcode(self, zipcode: str) -> Optional[str]:
        """Get zonage ABC for a commune by zipcode (may return multiple)."""
        zonage = self.db.query(ZonageCommune).filter(
            ZonageCommune.code_postal == zipcode
        ).first()
        
        if zonage:
            return zonage.zonage
        return None
