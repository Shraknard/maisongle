"""Commune lookup via the official API Découpage Administratif (geo.api.gouv.fr).

Replaces Melo's /cities endpoint for:
- city autocomplete (frontend search box)
- resolving an INSEE code to a Commune (name needed by scrapers)
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Dict, List, Optional, Tuple

import httpx

from app.scrapers.base import Commune

logger = logging.getLogger("services.geo")

GEO_BASE = "https://geo.api.gouv.fr"
HEADERS = {"User-Agent": "maisongle/1.0"}
FIELDS = "nom,code,codesPostaux,centre,population"

EARTH_RADIUS_KM = 6371.0
# A department is a candidate when its centroid is within radius + this buffer
# of the search point — covers the circumradius of the largest French
# departments (plus slack for the approximate centroids) so none that actually
# intersects the circle is skipped (communes are then filtered precisely).
DEPT_BUFFER_KM = 100.0
# Pad the commune centre test so communes straddling the circle boundary are
# kept; the DB read trims results to the exact circle anyway.
COMMUNE_PAD_KM = 3.0
# Cap the number of scraped communes (nearest first) to bound an on-demand run.
MAX_RADIUS_COMMUNES = 60

# Department centroids (mean of commune centres, geo.api.gouv.fr). geo.api does
# not expose department geometry, so this static table prefilters which
# departments a radius search must enumerate. Coarse on purpose — DEPT_BUFFER_KM
# absorbs the approximation; commune-level filtering does the precise work.
_DEPT_CENTROIDS = {
    "01": (46.09, 5.32), "02": (49.555, 3.53), "03": (46.352, 3.183),
    "04": (44.075, 6.145), "05": (44.579, 6.132), "06": (43.848, 7.106),
    "07": (44.783, 4.457), "08": (49.615, 4.654), "09": (42.968, 1.525),
    "10": (48.301, 4.184), "11": (43.12, 2.334), "12": (44.287, 2.599),
    "13": (43.558, 5.23), "14": (49.152, -0.324), "15": (45.047, 2.654),
    "16": (45.7, 0.151), "17": (45.774, -0.649), "18": (47.033, 2.522),
    "19": (45.315, 1.844), "21": (47.38, 4.791), "22": (48.484, -2.855),
    "23": (46.092, 2.033), "24": (45.064, 0.742), "25": (47.227, 6.367),
    "26": (44.694, 5.133), "27": (49.141, 1.004), "28": (48.439, 1.39),
    "29": (48.294, -4.135), "2A": (41.902, 8.949), "2B": (42.439, 9.28),
    "30": (44.023, 4.197), "31": (43.335, 1.138), "32": (43.663, 0.462),
    "33": (44.817, -0.345), "34": (43.588, 3.423), "35": (48.188, -1.635),
    "36": (46.771, 1.611), "37": (47.261, 0.665), "38": (45.318, 5.488),
    "39": (46.768, 5.676), "40": (43.81, -0.735), "41": (47.648, 1.307),
    "42": (45.722, 4.197), "43": (45.14, 3.762), "44": (47.338, -1.68),
    "45": (47.962, 2.341), "46": (44.645, 1.633), "47": (44.392, 0.481),
    "48": (44.55, 3.49), "49": (47.38, -0.487), "50": (49.108, -1.345),
    "51": (48.961, 4.194), "52": (48.111, 5.257), "53": (48.145, -0.671),
    "54": (48.782, 6.168), "55": (49.017, 5.383), "56": (47.81, -2.781),
    "57": (49.047, 6.622), "58": (47.144, 3.491), "59": (50.417, 3.259),
    "60": (49.43, 2.422), "61": (48.64, 0.103), "62": (50.459, 2.338),
    "63": (45.731, 3.173), "64": (43.324, -0.663), "65": (43.136, 0.209),
    "66": (42.608, 2.557), "67": (48.676, 7.546), "68": (47.801, 7.274),
    "69": (45.854, 4.664), "70": (47.627, 6.1), "71": (46.618, 4.622),
    "72": (48.041, 0.226), "73": (45.543, 6.184), "74": (46.07, 6.297),
    "75": (48.859, 2.347), "76": (49.666, 0.983), "77": (48.662, 2.916),
    "78": (48.853, 1.84), "79": (46.501, -0.305), "80": (49.941, 2.31),
    "81": (43.803, 2.113), "82": (44.063, 1.216), "83": (43.432, 6.218),
    "84": (44.012, 5.163), "85": (46.651, -1.254), "86": (46.608, 0.415),
    "87": (45.886, 1.238), "88": (48.234, 6.322), "89": (47.852, 3.61),
    "90": (47.625, 6.933), "91": (48.551, 2.263), "92": (48.845, 2.253),
    "93": (48.91, 2.467), "94": (48.783, 2.457), "95": (49.076, 2.15),
    "971": (16.155, -61.549), "972": (14.662, -61.038), "973": (4.522, -53.045),
    "974": (-21.127, 55.508), "976": (-12.818, 45.148),
}

# Communes are static; cache INSEE -> Commune for the process lifetime.
_COMMUNE_CACHE: Dict[str, Commune] = {}
# Per-department commune lists are static too; cached on first use.
_DEPT_COMMUNES: Dict[str, List[Commune]] = {}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def normalize_insee_code(code: str) -> str:
    """Normalize INSEE code - map Paris/Lyon/Marseille arrondissements to main city code.

    Bien'ici tags ads with the arrondissement INSEE (e.g. 69381 for Lyon 1er),
    while ``reverse_commune``/autocomplete resolve to the parent commune (69123).
    Both storage and search filtering must agree, so they both normalize.
    """
    code = code.zfill(5)
    if code.startswith('751') and len(code) == 5:
        return '75056'
    if code.startswith('6938') and len(code) == 5:
        return '69123'
    if code.startswith('132') and len(code) == 5 and '13201' <= code <= '13216':
        return '13055'
    return code


def _location_payload(commune: dict) -> dict:
    """Shape expected by the frontend autocomplete (legacy Melo-ish keys)."""
    centre = commune.get("centre") or {}
    coords = centre.get("coordinates") or [None, None]
    cps = commune.get("codesPostaux") or []
    insee = commune.get("code")
    return {
        "@id": insee,
        "name": commune.get("nom"),
        "displayName": commune.get("nom"),
        "zipcode": cps[0] if cps else None,
        "insee": insee,
        "latitude": coords[1],
        "longitude": coords[0],
    }


async def search_communes(query: str, limit: int = 10) -> List[dict]:
    """Autocomplete communes by name or postal code."""
    query = query.strip()
    if len(query) < 2:
        return []

    params = {"fields": FIELDS, "boost": "population", "limit": limit}
    if query.isdigit():
        params["codePostal"] = query
    else:
        params["nom"] = query

    try:
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
            resp = await client.get(f"{GEO_BASE}/communes", params=params)
            resp.raise_for_status()
            return [_location_payload(c) for c in resp.json()]
    except httpx.HTTPError as exc:
        logger.error("geo.api.gouv autocomplete a échoué (%s): %s", query, exc)
        return []


async def get_commune(insee: str) -> Optional[Commune]:
    """Resolve an INSEE code to a Commune (cached). Returns None if unknown."""
    if insee in _COMMUNE_CACHE:
        return _COMMUNE_CACHE[insee]

    try:
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
            resp = await client.get(f"{GEO_BASE}/communes/{insee}", params={"fields": FIELDS})
            if resp.status_code != 200:
                return None
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.error("geo.api.gouv lookup INSEE %s a échoué: %s", insee, exc)
        return None

    commune = _to_commune(data)
    _COMMUNE_CACHE[insee] = commune
    return commune


async def reverse_commune(lat: float, lon: float) -> Optional[Commune]:
    """Find the commune containing a coordinate (for radius searches)."""
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
            resp = await client.get(
                f"{GEO_BASE}/communes",
                params={"lat": lat, "lon": lon, "fields": FIELDS},
            )
            resp.raise_for_status()
            results = resp.json()
    except httpx.HTTPError as exc:
        logger.error("geo.api.gouv reverse (%s,%s) a échoué: %s", lat, lon, exc)
        return None

    if not results:
        return None
    commune = _to_commune(results[0])
    _COMMUNE_CACHE[commune.insee] = commune
    return commune


async def _dept_communes(client: httpx.AsyncClient, code: str) -> List[Commune]:
    """All communes of a department, with centres (cached per department)."""
    if code not in _DEPT_COMMUNES:
        resp = await client.get(
            f"{GEO_BASE}/departements/{code}/communes", params={"fields": FIELDS}
        )
        resp.raise_for_status()
        _DEPT_COMMUNES[code] = [
            _to_commune(c) for c in resp.json() if (c.get("centre") or {}).get("coordinates")
        ]
    return _DEPT_COMMUNES[code]


async def communes_within_radius(
    lat: float, lon: float, radius_km: float, limit: int = MAX_RADIUS_COMMUNES
) -> List[Commune]:
    """Communes whose centre lies within ``radius_km`` of a point, nearest first.

    Enumerates communes of the departments overlapping the circle, keeps those
    within range (plus a small pad), and always includes the commune containing
    the point so large host communes aren't dropped when their centre sits
    outside the radius. Capped to ``limit`` to bound the on-demand scrape.
    """
    candidates = [
        code for code, (clat, clon) in _DEPT_CENTROIDS.items()
        if haversine_km(lat, lon, clat, clon) <= radius_km + DEPT_BUFFER_KM
    ]

    scored: List[Tuple[float, Commune]] = []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            lists = await asyncio.gather(
                *(_dept_communes(client, code) for code in candidates)
            )
            for communes in lists:
                for commune in communes:
                    if commune.latitude is None or commune.longitude is None:
                        continue
                    dist = haversine_km(lat, lon, commune.latitude, commune.longitude)
                    if dist <= radius_km + COMMUNE_PAD_KM:
                        scored.append((dist, commune))
                        _COMMUNE_CACHE[commune.insee] = commune
    except httpx.HTTPError as exc:
        logger.error("communes_within_radius (%s,%s,%s) a échoué: %s", lat, lon, radius_km, exc)
        return []

    scored.sort(key=lambda item: item[0])
    chosen = [commune for _, commune in scored[:limit]]

    # Guarantee the host commune is present (handles large communes whose centre
    # falls outside the circle, e.g. a point in the outskirts of Marseille).
    host = await reverse_commune(lat, lon)
    if host and all(c.insee != host.insee for c in chosen):
        chosen.insert(0, host)
    return chosen


def _to_commune(data: dict) -> Commune:
    centre = data.get("centre") or {}
    coords = centre.get("coordinates") or [None, None]
    cps = data.get("codesPostaux") or []
    return Commune(
        insee=data.get("code", ""),
        name=data.get("nom", ""),
        zipcode=cps[0] if cps else None,
        latitude=coords[1],
        longitude=coords[0],
    )
