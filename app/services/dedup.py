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


def dedup_key(geohash: Optional[str], surface: Optional[float], room: Optional[int]) -> Optional[str]:
    """Fuzzy clustering key: ~150 m cell + rounded surface + rooms."""
    if not geohash:
        return None
    gh = geohash[:7]
    surf = int(round(surface)) if surface else 0
    rooms = room or 0
    return f"{gh}|{surf}|{rooms}"
