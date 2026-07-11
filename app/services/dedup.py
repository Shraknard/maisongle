"""Geohash + cross-source deduplication helpers.

Identity within a source is ``(source, source_id)``. The ``dedup_key`` here is a
fuzzy fingerprint used to *cluster* the same property listed on several sources,
not to merge them — it tolerates small variations by rounding.
"""
from __future__ import annotations

from typing import Optional

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat: Optional[float], lon: Optional[float], precision: int = 7) -> Optional[str]:
    """Encode (lat, lon) to a geohash. Precision 7 ≈ 150 m cells."""
    if lat is None or lon is None:
        return None

    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    bits = [16, 8, 4, 2, 1]
    out = []
    bit = 0
    ch = 0
    even = True

    while len(out) < precision:
        if even:
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon > mid:
                ch |= bits[bit]
                lon_range[0] = mid
            else:
                lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat > mid:
                ch |= bits[bit]
                lat_range[0] = mid
            else:
                lat_range[1] = mid
        even = not even

        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit = 0
            ch = 0

    return "".join(out)


def dedup_key(
    insee: Optional[str],
    transaction_type: Optional[int],
    property_type: Optional[int],
    surface: Optional[float],
    room: Optional[int],
    price: Optional[float],
) -> Optional[str]:
    """Coordinate-independent fuzzy fingerprint clustering the same property
    across sources.

    Earlier this keyed on a ~150 m geohash cell, which meant the two sources that
    carry no coordinates (PAP, SeLoger, and now notaires/ParuVendu) could never be
    deduplicated, and geocoding them to a commune centroid would over-merge every
    same-size flat in the commune. Instead we key on the stable attributes every
    source exposes — commune (INSEE, already normalized to the parent city), sale
    vs rent, property type, rounded surface, rooms and a coarse price bucket.

    The price bucket (relative to the transaction) both tolerates small
    cross-source price differences and — crucially for big communes where every
    Paris/Lyon/Marseille arrondissement collapses to one INSEE — stops two
    genuinely different flats of the same size/rooms from being merged unless they
    also share a price.

    Returns None (never clustered) when the core identity is too thin to trust.
    """
    if not insee or not surface:
        return None
    surf = int(round(surface))
    rooms = room if room is not None else 0
    ptype = property_type if property_type is not None else -1
    tx = transaction_type if transaction_type is not None else -1
    # ~1.6 % tolerance on sales (5 k€ step), ~one bucket per 50 €/mo on rents.
    step = 50 if tx == 1 else 5000
    bucket = int(round(price / step)) if price else 0
    return f"{insee}|{tx}|{ptype}|{surf}|{rooms}|{bucket}"
