from __future__ import annotations

from typing import Dict, List, Optional

from app.scrapers.base import BaseScraper
from app.scrapers.bienici import BienIciScraper
from app.scrapers.leboncoin import LeboncoinScraper
from app.scrapers.logicimmo import LogicImmoScraper
from app.scrapers.notaires import NotairesScraper
from app.scrapers.pap import PapScraper
from app.scrapers.paruvendu import ParuVenduScraper
from app.scrapers.seloger import SelogerScraper

# Adding a new source = import its scraper and add an instance here.
_INSTANCES: List[BaseScraper] = [
    BienIciScraper(),
    PapScraper(),
    NotairesScraper(),   # free/open JSON API — notary sales (unique inventory)
    ParuVenduScraper(),  # free/open HTML — validated live
    LeboncoinScraper(),  # no-op unless leboncoin is enabled + a transport is set
    SelogerScraper(),    # no-op unless seloger is enabled + a transport is set
    LogicImmoScraper(),  # scaffold: no-op unless logicimmo is enabled + a transport
]

SCRAPERS: Dict[str, BaseScraper] = {s.source: s for s in _INSTANCES}


def get_scrapers(sources: Optional[List[str]] = None) -> List[BaseScraper]:
    """Return enabled scrapers, optionally filtered by source name."""
    if not sources:
        return list(SCRAPERS.values())
    return [SCRAPERS[s] for s in sources if s in SCRAPERS]
