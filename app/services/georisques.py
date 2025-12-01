import httpx
from typing import Optional, Dict, Any, List
from app.config import get_settings


class GeorisquesService:
    """Service for interacting with Georisques API."""
    
    def __init__(self):
        settings = get_settings()
        self.base_url = settings.georisques_api_base_url
    
    async def get_all_risks(
        self, 
        latitude: Optional[float] = None, 
        longitude: Optional[float] = None,
        code_insee: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get all georisques for a location."""
        
        risks = {}
        
        # Get general risks summary for the commune
        if code_insee:
            risks["risques_commune"] = await self.get_risques_commune(code_insee=code_insee)
            risks["radon"] = await self.get_radon(code_insee=code_insee)
            risks["catnat"] = await self.get_catnat(code_insee=code_insee)
        
        # Get risks by coordinates if available
        if latitude is not None and longitude is not None:
            risks["inondations"] = await self.get_inondations(lat=latitude, lon=longitude)
            risks["argiles"] = await self.get_argiles(lat=latitude, lon=longitude)
            risks["cavites"] = await self.get_cavites(lat=latitude, lon=longitude)
            risks["mouvements_terrain"] = await self.get_mouvements_terrain(lat=latitude, lon=longitude)
            risks["installations_classees"] = await self.get_installations_classees(lat=latitude, lon=longitude)
            risks["sols_pollues"] = await self.get_sols_pollues(lat=latitude, lon=longitude)
        
        return risks
    
    async def get_risques_commune(self, code_insee: str) -> Optional[Dict[str, Any]]:
        """Get general risks summary for a commune."""
        return await self._make_request("gaspar/risques", {"code_insee": code_insee})
    
    async def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Make a request to the Georisques API."""
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/{endpoint}",
                    params=params,
                )
                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"Georisques API error ({endpoint}): status {response.status_code}")
                return None
            except Exception as e:
                print(f"Georisques API error ({endpoint}): {e}")
                return None
    
    async def get_radon(self, code_insee: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get radon risk level for a commune."""
        if not code_insee:
            return None
        return await self._make_request("radon", {"code_insee": code_insee})
    
    async def get_inondations(self, lat: float, lon: float, rayon: int = 1000) -> Optional[Dict[str, Any]]:
        """Get flood risks around coordinates."""
        return await self._make_request(
            "gaspar/azi", 
            {"latlon": f"{lon},{lat}", "rayon": rayon}
        )
    
    async def get_argiles(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """Get clay shrink-swell risk (RGA - Retrait Gonflement Argiles)."""
        # L'endpoint rga retourne directement l'exposition, pas un format paginé
        result = await self._make_request("rga", {"latlon": f"{lon},{lat}"})
        if result and "exposition" in result:
            # Convertir au format attendu
            return {"data": [result]}
        return result
    
    async def get_cavites(self, lat: float, lon: float, rayon: int = 1000) -> Optional[Dict[str, Any]]:
        """Get underground cavities around coordinates."""
        return await self._make_request(
            "cavites",
            {"latlon": f"{lon},{lat}", "rayon": rayon}
        )
    
    async def get_mouvements_terrain(self, lat: float, lon: float, rayon: int = 1000) -> Optional[Dict[str, Any]]:
        """Get ground movement risks."""
        return await self._make_request(
            "mvt_terrains",
            {"latlon": f"{lon},{lat}", "rayon": rayon}
        )
    
    async def get_installations_classees(self, lat: float, lon: float, rayon: int = 2000) -> Optional[Dict[str, Any]]:
        """Get classified installations (ICPE) around coordinates."""
        return await self._make_request(
            "installations_classees",
            {"latlon": f"{lon},{lat}", "rayon": rayon}
        )
    
    async def get_sols_pollues(self, lat: float, lon: float, rayon: int = 1000) -> Optional[Dict[str, Any]]:
        """Get polluted soils (SIS/BASIAS) around coordinates."""
        return await self._make_request(
            "sis",
            {"latlon": f"{lon},{lat}", "rayon": rayon}
        )
    
    async def get_catnat(self, code_insee: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get natural disaster declarations for a commune."""
        if not code_insee:
            return None
        return await self._make_request("gaspar/catnat", {"code_insee": code_insee})
