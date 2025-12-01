from sqlalchemy import Column, String, Integer
from app.database import Base


class ZonageCommune(Base):
    """Model for commune zonage ABC data."""
    __tablename__ = "zonage_communes"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Commune identifiers
    code_insee = Column(String(10), unique=True, nullable=False, index=True)
    nom_commune = Column(String(255), nullable=True)
    code_postal = Column(String(10), nullable=True)
    
    # Department/Region
    code_departement = Column(String(5), nullable=True)
    nom_departement = Column(String(255), nullable=True)
    code_region = Column(String(5), nullable=True)
    nom_region = Column(String(255), nullable=True)
    
    # Zonage ABC classification
    zonage = Column(String(5), nullable=False)  # A, Abis, B1, B2, C
    
    def __repr__(self):
        return f"<ZonageCommune {self.code_insee}: {self.nom_commune} - Zone {self.zonage}>"
