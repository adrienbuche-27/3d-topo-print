"""Shared geographic helpers: distances and local metric projections."""

from __future__ import annotations

import math

from pyproj import CRS, Transformer

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def utm_crs_for(lon: float, lat: float) -> CRS:
    """UTM zone (WGS84) containing the given point."""
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def to_crs(crs: CRS) -> Transformer:
    """Transformer from WGS84 lon/lat to `crs` (always_xy: x=lon/easting, y=lat/northing)."""
    return Transformer.from_crs("EPSG:4326", crs, always_xy=True)


def from_crs(crs: CRS) -> Transformer:
    """Transformer from `crs` back to WGS84 lon/lat."""
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
