from sqlalchemy import Column, String, Integer, DateTime, Text, JSON
from sqlalchemy.sql import func
from app.database import Base


class SavedSearch(Base):
    """Model for saved searches stored in database."""
    __tablename__ = "saved_searches"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Search name (user-defined)
    name = Column(String(255), nullable=False)
    
    # Location filters
    department = Column(String(10), nullable=True)
    city_id = Column(String(255), nullable=True)  # e.g., /cities/30950 (legacy)
    city_name = Column(String(255), nullable=True)  # Display name (legacy)
    city_insee = Column(String(10), nullable=True)
    zipcode = Column(String(10), nullable=True)
    
    # Multiple cities (new)
    selected_cities = Column(JSON, nullable=True)  # [{id, name, lat, lon, insee}, ...]
    
    # Property filters
    property_type = Column(Integer, nullable=True)  # 0=apartment, 1=house, etc.
    transaction_type = Column(Integer, default=0)  # 0=sale, 1=rent
    
    # Price filters
    budget_min = Column(Integer, nullable=True)
    budget_max = Column(Integer, nullable=True)
    
    # Surface filters
    surface_min = Column(Integer, nullable=True)
    surface_max = Column(Integer, nullable=True)
    
    # Room filters
    room_min = Column(Integer, nullable=True)
    room_max = Column(Integer, nullable=True)
    bedroom_min = Column(Integer, nullable=True)
    bedroom_max = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<SavedSearch {self.id}: {self.name}>"
