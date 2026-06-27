from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


class SavedSearchCreate(BaseModel):
    """Schema for creating a saved search."""
    name: str
    
    # Location filters
    department: Optional[str] = None
    selected_cities: Optional[List[Any]] = None  # [{id, name, lat, lon, insee}, ...]

    # Property filters
    property_type: Optional[int] = None
    transaction_type: Optional[int] = 0
    
    # Price filters
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    
    # Surface filters
    surface_min: Optional[int] = None
    surface_max: Optional[int] = None
    
    # Room filters
    room_min: Optional[int] = None
    room_max: Optional[int] = None
    bedroom_min: Optional[int] = None
    bedroom_max: Optional[int] = None


class SavedSearchResponse(BaseModel):
    """Schema for saved search response."""
    id: int
    name: str
    
    # Location filters
    department: Optional[str] = None
    selected_cities: Optional[List[Any]] = None  # [{id, name, lat, lon, insee}, ...]

    # Property filters
    property_type: Optional[int] = None
    transaction_type: Optional[int] = None
    
    # Price filters
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    
    # Surface filters
    surface_min: Optional[int] = None
    surface_max: Optional[int] = None
    
    # Room filters
    room_min: Optional[int] = None
    room_max: Optional[int] = None
    bedroom_min: Optional[int] = None
    bedroom_max: Optional[int] = None
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True
