from sqlalchemy import Column, String, Integer, Float, DateTime, Text, JSON
from sqlalchemy.sql import func
from app.database import Base


class Favorite(Base):
    """Model for favorite properties stored in database."""
    __tablename__ = "favorites"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Melo.io property identifiers
    property_uuid = Column(String(255), unique=True, nullable=False, index=True)
    advert_uuid = Column(String(255), nullable=True)
    
    # Basic property info
    title = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    price_per_meter = Column(Float, nullable=True)
    surface = Column(Float, nullable=True)
    land_surface = Column(Float, nullable=True)
    room = Column(Integer, nullable=True)
    bedroom = Column(Integer, nullable=True)
    floor = Column(Integer, nullable=True)
    
    # Property type: 0=apartment, 1=house, etc.
    property_type = Column(Integer, nullable=True)
    # Transaction type: 0=sale, 1=rent
    transaction_type = Column(Integer, nullable=True)
    
    # Location
    city_name = Column(String(255), nullable=True)
    city_zipcode = Column(String(10), nullable=True)
    city_insee = Column(String(10), nullable=True)
    department_code = Column(String(5), nullable=True)
    department_name = Column(String(255), nullable=True)
    region_name = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # Energy
    energy_category = Column(String(5), nullable=True)
    energy_value = Column(Integer, nullable=True)
    ghg_category = Column(String(5), nullable=True)
    ghg_value = Column(Integer, nullable=True)
    
    # Contact info
    agency = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    contact_email = Column(String(255), nullable=True)
    
    # URLs
    url = Column(String(1000), nullable=True)
    pictures = Column(JSON, nullable=True)  # List of picture URLs
    
    # Raw data from Melo.io (for reference)
    raw_data = Column(JSON, nullable=True)
    
    # Enrichment data
    zonage_abc = Column(String(5), nullable=True)  # A, B1, B2, C
    georisques = Column(JSON, nullable=True)  # All georisques data
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<Favorite {self.property_uuid}: {self.title}>"
