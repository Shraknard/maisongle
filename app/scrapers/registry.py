from __future__ import annotations

from typing import Dict, List, Optional

from app.scrapers.base import BaseScraper
from app.scrapers.bienici import BienIciScraper

# Adding a new source = import its scraper and add an instance here.
_INSTANCES: List[BaseScraper] = [
    BienIciScraper(),
]

SCRAPERS: Dict[str, BaseScraper] = {s.source: s for s in _INSTANCES}


def get_scrapers(sources: Optional[List[str]] = None) -> List[BaseScraper]:
    """Return enabled scrapers, optionally filtered by source name."""
    if not sources:
        return list(SCRAPERS.values())
    return [SCRAPERS[s] for s in sources if s in SCRAPERS]
