"""Serialize Listing rows into the shape the Jinja templates consume.

The frontend was built around Melo's camelCase shape (``adverts``, ``city``,
``propertyType``/``transactionType``, ``location``). We keep that shape so the
existing templates work with minimal changes.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.listing import Listing, PriceHistory


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def serialize_listing(
    listing: Listing,
    price_drop: Optional[dict] = None,
    duplicates: Optional[dict] = None,
) -> dict:
    advert = {
        "url": listing.url,
        "site": listing.source,
        "description": listing.description,
        "energy": listing.energy_category,
        "greenHouseGas": listing.ghg_category,
        "createdAt": _iso(listing.first_seen),
        "updatedAt": _iso(listing.last_seen),
        "lastCrawledAt": _iso(listing.last_seen),
        "contact": None,
        "features": None,
    }
    location = {"lat": listing.latitude, "lon": listing.longitude}
    return {
        "uuid": listing.uid,
        "source": listing.source,
        "title": listing.title,
        "description": listing.description,
        "price": listing.price,
        "pricePerMeter": listing.price_per_meter,
        "surface": listing.surface,
        "landSurface": listing.land_surface,
        "room": listing.room,
        "bedroom": listing.bedroom,
        "floor": listing.floor,
        "propertyType": listing.property_type,
        "transactionType": listing.transaction_type,
        "pictures": listing.pictures or [],
        "city": {
            "name": listing.city_name,
            "zipcode": listing.city_zipcode,
            "insee": listing.city_insee,
            "department": listing.department_code,
            "location": location,
        },
        "location": location,
        "adverts": [advert] if listing.url else [],
        "energy": {"category": listing.energy_category, "value": listing.energy_value},
        "ghg": {"category": listing.ghg_category, "value": listing.ghg_value},
        "agency": listing.agency,
        "createdAt": _iso(listing.first_seen),
        "updatedAt": _iso(listing.last_seen),
        "lastCrawledAt": _iso(listing.last_seen),
        "priceDrop": price_drop,
        # When the same property was found on several sources, the others (+ the
        # lowest price seen). None when this listing is unique.
        "duplicates": duplicates,
    }


def price_drops(db: Session, listing_ids: List[int]) -> Dict[int, dict]:
    """Compute a price-drop summary per listing from its price history.

    A drop is reported only when the most recent price is below the first
    observed price.
    """
    if not listing_ids:
        return {}

    rows = (
        db.query(PriceHistory)
        .filter(PriceHistory.listing_id.in_(listing_ids))
        .order_by(PriceHistory.observed_at.asc())
        .all()
    )
    by_listing: Dict[int, List[float]] = {}
    for row in rows:
        by_listing.setdefault(row.listing_id, []).append(row.price)

    drops: Dict[int, dict] = {}
    for lid, prices in by_listing.items():
        if len(prices) < 2:
            continue
        initial, current = prices[0], prices[-1]
        if current < initial and initial:
            drops[lid] = {
                "initial": initial,
                "current": current,
                "amount": initial - current,
                "pct": round((initial - current) / initial * 100, 1),
            }
    return drops


def price_history(db: Session, listing_id: int) -> List[dict]:
    rows = (
        db.query(PriceHistory)
        .filter(PriceHistory.listing_id == listing_id)
        .order_by(PriceHistory.observed_at.asc())
        .all()
    )
    return [{"price": r.price, "observedAt": _iso(r.observed_at)} for r in rows]
