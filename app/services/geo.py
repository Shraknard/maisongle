"""Commune lookup via the official API Découpage Administratif (geo.api.gouv.fr).

Replaces Melo's /cities endpoint for:
- city autocomplete (frontend search box)
- resolving an INSEE code to a Commune (name needed by scrapers)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx

from app.scrapers.base import Commune

logger = logging.getLogger("services.geo")

GEO_BASE = "https://geo.api.gouv.fr"
HEADERS = {"User-Agent": "maisongle/1.0"}
FIELDS = "nom,code,codesPostaux,centre,population"

# Communes are static; cache INSEE -> Commune for the process lifetime.
_COMMUNE_CACHE: Dict[str, Commune] = {}


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
