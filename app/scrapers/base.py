from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Commune:
    """A target commune for a search, resolved from INSEE via geo.api.gouv.fr."""
    insee: str
    name: str
    zipcode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@dataclass
class SearchCriteria:
    """Source-agnostic search parameters, built from a request or a SavedSearch.

    The ``signature`` is a stable hash of the perimeter, used to throttle
    re-scraping (5 min window) and to journal runs.
    """
    communes: List[Commune] = field(default_factory=list)
    transaction_type: int = 0                       # 0 = vente, 1 = location
    property_types: List[int] = field(default_factory=list)  # [] = tous types
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    surface_min: Optional[int] = None
    surface_max: Optional[int] = None
    room_min: Optional[int] = None
    room_max: Optional[int] = None
    bedroom_min: Optional[int] = None
    bedroom_max: Optional[int] = None

    def signature(self) -> str:
        payload = {
            "communes": sorted(c.insee for c in self.communes),
            "transaction_type": self.transaction_type,
            "property_types": sorted(self.property_types),
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "surface_min": self.surface_min,
            "surface_max": self.surface_max,
            "room_min": self.room_min,
            "room_max": self.room_max,
            "bedroom_min": self.bedroom_min,
            "bedroom_max": self.bedroom_max,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode()).hexdigest()


@dataclass
class NormalizedListing:
    """Normalized output of every scraper — mirrors the Listing/Favorite shape."""
    source: str
    source_id: str
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
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    energy_category: Optional[str] = None
    energy_value: Optional[int] = None
    ghg_category: Optional[str] = None
    ghg_value: Optional[int] = None
    agency: Optional[str] = None
    url: Optional[str] = None
    pictures: List[str] = field(default_factory=list)


class BaseScraper(ABC):
    """Common interface for every platform scraper.

    A scraper handles its own pagination, rate-limiting, parsing and
    normalization. It must never raise on a single bad listing — skip it.
    Network/structure errors may propagate; the scrape service isolates each
    source so one failure does not abort the run.
    """
    source: str = "base"

    @abstractmethod
    async def search(self, criteria: SearchCriteria) -> List[NormalizedListing]:
        """Fetch and normalize listings matching ``criteria``."""
        raise NotImplementedError
