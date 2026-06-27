from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


class FavoriteCreate(BaseModel):
    """Schema for creating a favorite — all property data provided directly."""
    property_uuid: str

    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    price_per_meter: Optional[float] = None
    surface: Optional[float] = None
    land_surface: Optional[float] = None
    room: Optional[int] = None
    bedroom: Optional[int] = None
    floor: Optional[int] = None
    property_type: Optional[int] = None
    transaction_type: Optional[int] = None

    city_name: Optional[str] = None
    city_zipcode: Optional[str] = None
    city_insee: Optional[str] = None
    department_code: Optional[str] = None
    department_name: Optional[str] = None
    region_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    energy_category: Optional[str] = None
    energy_value: Optional[int] = None
    ghg_category: Optional[str] = None
    ghg_value: Optional[int] = None

    agency: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None

    url: Optional[str] = None
    pictures: Optional[List[str]] = None


class FavoriteResponse(BaseModel):
    """Schema for favorite response."""
    id: int
    property_uuid: str

    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    price_per_meter: Optional[float] = None
    surface: Optional[float] = None
    land_surface: Optional[float] = None
    room: Optional[int] = None
    bedroom: Optional[int] = None
    floor: Optional[int] = None
    property_type: Optional[int] = None
    transaction_type: Optional[int] = None

    city_name: Optional[str] = None
    city_zipcode: Optional[str] = None
    city_insee: Optional[str] = None
    department_code: Optional[str] = None
    department_name: Optional[str] = None
    region_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    energy_category: Optional[str] = None
    energy_value: Optional[int] = None
    ghg_category: Optional[str] = None
    ghg_value: Optional[int] = None

    agency: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None

    url: Optional[str] = None
    pictures: Optional[List[str]] = None

    zonage_abc: Optional[str] = None
    georisques: Optional[Any] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
