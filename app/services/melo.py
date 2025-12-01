import httpx
from typing import Optional, Dict, Any, List
from app.config import get_settings


class MeloService:
    """Service for interacting with Melo.io API."""
    
    def __init__(self):
        settings = get_settings()
        self.base_url = settings.melo_api_base_url
        self.api_key = settings.melo_api_key
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key,
        }
    
    async def search_properties(
        self,
        department: Optional[str] = None,
        city_id: Optional[List[str]] = None,
        city_insee: Optional[str] = None,
        zipcode: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius: Optional[int] = None,
        property_types: Optional[List[int]] = None,
        transaction_type: Optional[int] = 0,
        budget_min: Optional[int] = None,
        budget_max: Optional[int] = None,
        surface_min: Optional[int] = None,
        surface_max: Optional[int] = None,
        room_min: Optional[int] = None,
        room_max: Optional[int] = None,
        bedroom_min: Optional[int] = None,
        bedroom_max: Optional[int] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """Search properties with given filters."""
        
        params = {}
        
        # Location filters
        if department:
            params["includedDepartments[]"] = f"departments/{department}"
        if city_id:
            # city_id comes as "/cities/12345" from autocomplete - can be a list
            for cid in city_id:
                if "includedCities[]" not in params:
                    params["includedCities[]"] = []
                if isinstance(params["includedCities[]"], list):
                    params["includedCities[]"].append(cid)
                else:
                    params["includedCities[]"] = [params["includedCities[]"], cid]
        if city_insee:
            params["includedInseeCodes[]"] = city_insee
        if zipcode:
            params["includedZipcodes[]"] = zipcode
        if lat is not None:
            params["lat"] = lat
        if lon is not None:
            params["lon"] = lon
        if radius is not None:
            params["radius"] = radius
        
        # Property filters
        if property_types:
            # Can be a list of types
            for pt in property_types:
                if "propertyTypes[]" not in params:
                    params["propertyTypes[]"] = []
                if isinstance(params["propertyTypes[]"], list):
                    params["propertyTypes[]"].append(pt)
                else:
                    params["propertyTypes[]"] = [params["propertyTypes[]"], pt]
        if transaction_type is not None:
            params["transactionType"] = transaction_type
        
        # Price filters
        if budget_min is not None:
            params["budgetMin"] = budget_min
        if budget_max is not None:
            params["budgetMax"] = budget_max
        
        # Surface filters
        if surface_min is not None:
            params["surfaceMin"] = surface_min
        if surface_max is not None:
            params["surfaceMax"] = surface_max
        
        # Room filters
        if room_min is not None:
            params["roomMin"] = room_min
        if room_max is not None:
            params["roomMax"] = room_max
        if bedroom_min is not None:
            params["bedroomMin"] = bedroom_min
        if bedroom_max is not None:
            params["bedroomMax"] = bedroom_max
        
        # Only get properties with location for map display
        params["withLocation"] = "true"
        
        # Sorting
        if sort_by:
            # Map frontend sort names to API field names
            sort_fields = {
                'price': 'price',
                'surface': 'surface',
                'date': 'createdAt',
                'updated': 'updatedAt',
            }
            if sort_by in sort_fields:
                order = 'desc' if sort_order == 'desc' else 'asc'
                params["order[" + sort_fields[sort_by] + "]"] = order
        
        # Pagination
        params["page"] = page
        params["itemsPerPage"] = 50
        
        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/documents/properties",
                    params=params,
                    headers=self._get_headers(),
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()
                
                # Extract properties and pagination info
                properties = data.get("hydra:member", [])
                total_items = data.get("hydra:totalItems", 0)
                
                return {
                    "properties": properties,
                    "total": total_items,
                    "page": page,
                }
            except httpx.HTTPError as e:
                print(f"Melo API error: {e}")
                return {"properties": [], "total": 0, "page": page, "error": str(e)}
    
    async def get_property(self, property_uuid: str) -> Optional[Dict[str, Any]]:
        """Get a single property by UUID."""
        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/documents/properties/{property_uuid}",
                    headers=self._get_headers(),
                    timeout=30.0
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                print(f"Melo API error getting property {property_uuid}: {e}")
                return None
    
    async def search_locations(self, query: str) -> List[Dict[str, Any]]:
        """Search for cities/locations by name or zipcode."""
        async with httpx.AsyncClient(verify=False) as client:
            try:
                # Use /cities endpoint with name filter
                response = await client.get(
                    f"{self.base_url}/cities",
                    params={"name": query},
                    headers=self._get_headers(),
                    timeout=15.0
                )
                response.raise_for_status()
                data = response.json()
                cities = data.get("hydra:member", [])
                
                # Transform to match expected format for frontend
                return [
                    {
                        "@id": city.get("@id"),
                        "name": city.get("originalName") or city.get("name"),
                        "displayName": city.get("originalName") or city.get("name"),
                        "zipcode": city.get("zipcode"),
                        "insee": city.get("insee"),
                        "latitude": city.get("latitude"),
                        "longitude": city.get("longitude"),
                    }
                    for city in cities
                ]
            except httpx.HTTPError as e:
                print(f"Melo API error searching locations: {e}")
                return []
