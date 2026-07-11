"""On-demand scraping with a per-perimeter throttle.

The web app never runs a scheduler. When the search endpoint is hit, it calls
``refresh_if_stale``: if this exact perimeter (criteria signature) was scraped
less than ``ttl`` seconds ago, we serve the DB cache; otherwise we run every
scraper, upsert the results, and track price changes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.listing import Listing, PriceHistory, Hidden, ScrapeRun
from app.scrapers.base import SearchCriteria, NormalizedListing
from app.scrapers.registry import get_scrapers
from app.services.dedup import geohash_encode, dedup_key
from app.services.geo import normalize_insee_code

logger = logging.getLogger("services.scrape")

DEFAULT_TTL = 300  # 5 minutes
# A listing not re-seen for this long is deactivated (sold/rented/delisted). The
# window is generous on purpose: sources with capped pagination (SeLoger 30/type,
# Leboncoin/PAP page caps on refresh) don't return their whole catalogue each run,
# so a short window would wrongly retire deep listings. Re-seeing one reactivates
# it (``_upsert`` sets ``active = True``), so this self-heals.
STALE_DAYS = 14

_LISTING_FIELDS = (
    "title", "description", "price", "price_per_meter", "surface", "land_surface",
    "room", "bedroom", "floor", "property_type", "transaction_type",
    "city_name", "city_zipcode", "city_insee", "department_code",
    "latitude", "longitude", "energy_category", "energy_value",
    "ghg_category", "ghg_value", "agency", "url", "pictures",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def refresh_if_stale(
    db: Session,
    criteria: SearchCriteria,
    ttl: int = DEFAULT_TTL,
    sources: Optional[List[str]] = None,
) -> dict:
    """Scrape this perimeter unless it was refreshed within ``ttl`` seconds."""
    signature = criteria.signature()
    last = (
        db.query(func.max(ScrapeRun.started_at))
        .filter(ScrapeRun.signature == signature)
        .scalar()
    )
    if last is not None:
        age = _now() - _as_aware(last)
        if age < timedelta(seconds=ttl):
            return {"scraped": False, "age_seconds": int(age.total_seconds())}

    # No prior run for this perimeter -> first scrape: backfill the catalogue
    # deeply; later refreshes only fetch the newest pages.
    criteria.first_scrape = last is None
    return await _run(db, criteria, signature, sources)


async def _run(
    db: Session, criteria: SearchCriteria, signature: str, sources: Optional[List[str]]
) -> dict:
    run = ScrapeRun(signature=signature, started_at=_now(), status="ok")
    db.add(run)
    db.flush()

    hidden = {(h.source, h.source_id) for h in db.query(Hidden.source, Hidden.source_id).all()}
    detail: dict = {}
    total_found = 0
    total_new = 0

    # Scrape every source concurrently (network I/O is the slow part); the DB
    # upserts below stay sequential since the Session is not concurrency-safe.
    scrapers = get_scrapers(sources)
    outcomes = await asyncio.gather(
        *(s.search(criteria) for s in scrapers), return_exceptions=True
    )

    for scraper, outcome in zip(scrapers, outcomes):
        if isinstance(outcome, Exception):  # isolate each source
            logger.error("scraper %s a échoué: %s", scraper.source, outcome)
            detail[scraper.source] = {"error": str(outcome)}
            run.status = "partial"
            continue
        items = outcome
        n_new = _upsert(db, scraper.source, items, hidden)
        entry = {"found": len(items), "new": n_new}
        # Paid transports (Scrapfly) expose the credits billed by this run.
        cost = getattr(scraper, "last_cost", None)
        if cost:
            entry["cost_credits"] = cost
        detail[scraper.source] = entry
        total_found += len(items)
        total_new += n_new
        logger.info("%s: %d annonces, %d nouvelles", scraper.source, len(items), n_new)

    _deactivate_stale(db)

    run.finished_at = _now()
    run.n_found = total_found
    run.n_new = total_new
    run.detail = detail
    db.commit()

    return {"scraped": True, "found": total_found, "new": total_new, "detail": detail}


def _upsert(
    db: Session, source: str, items: List[NormalizedListing], hidden: set
) -> int:
    now = _now()
    n_new = 0

    for item in items:
        if (source, item.source_id) in hidden:
            continue

        gh = geohash_encode(item.latitude, item.longitude)

        existing = (
            db.query(Listing)
            .filter(Listing.source == source, Listing.source_id == item.source_id)
            .first()
        )

        if existing:
            if item.price and existing.price != item.price:
                db.add(PriceHistory(listing_id=existing.id, price=item.price))
            _apply(existing, item, gh)
            existing.last_seen = now
            existing.active = True
        else:
            listing = Listing(source=source, source_id=item.source_id)
            _apply(listing, item, gh)
            listing.first_seen = now
            listing.last_seen = now
            listing.active = True
            db.add(listing)
            db.flush()
            if item.price:
                db.add(PriceHistory(listing_id=listing.id, price=item.price))
            n_new += 1

    return n_new


def _deactivate_stale(db: Session) -> None:
    """Retire listings not re-seen for ``STALE_DAYS`` (see the constant's note)."""
    cutoff = _now() - timedelta(days=STALE_DAYS)
    db.query(Listing).filter(
        Listing.active.is_(True), Listing.last_seen < cutoff
    ).update({Listing.active: False}, synchronize_session=False)


def _apply(listing: Listing, item: NormalizedListing, gh) -> None:
    for fld in _LISTING_FIELDS:
        setattr(listing, fld, getattr(item, fld))
    # Normalize PLM arrondissement INSEE to the parent commune so listings are
    # found when searching by the parent commune (matches the search filter).
    if listing.city_insee:
        listing.city_insee = normalize_insee_code(listing.city_insee)
    listing.geohash = gh
    # Cross-source dedup fingerprint — computed from the normalized fields so all
    # sources (with or without coordinates) cluster on the same key.
    listing.dedup_key = dedup_key(
        listing.city_insee, listing.transaction_type, listing.property_type,
        listing.surface, listing.room, listing.price,
    )
