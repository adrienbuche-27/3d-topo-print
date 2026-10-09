"""GPX loading: parsing, cleaning, simplification, stats and print framing."""

from __future__ import annotations

from dataclasses import dataclass, field

import gpxpy
import gpxpy.gpx
import numpy as np

from .geo import from_crs, haversine_m, to_crs, utm_crs_for

# Elevations outside this range are treated as sensor garbage and dropped.
MIN_VALID_ELE_M = -500.0
MAX_VALID_ELE_M = 9_000.0


class GpxError(ValueError):
    """Raised when a GPX file cannot be used."""


@dataclass(frozen=True)
class TrackPoint:
    lat: float
    lon: float
    ele: float | None = None


@dataclass
class Track:
    name: str | None
    # Each segment is drawn as its own line; gaps between segments are not bridged.
    segments: list[list[TrackPoint]] = field(default_factory=list)

    @property
    def points(self) -> list[TrackPoint]:
        return [p for seg in self.segments for p in seg]


@dataclass(frozen=True)
class TrackStats:
    distance_m: float
    ascent_m: float | None
    descent_m: float | None
    min_ele_m: float | None
    max_ele_m: float | None
    point_count: int


@dataclass(frozen=True)
class Frame:
    """Rectangular print area, defined in a local UTM projection (metres)."""

    epsg: int
    # (min_x, min_y, max_x, max_y) in metres in `epsg`.
    bounds_m: tuple[float, float, float, float]
    # (west, south, east, north) WGS84 envelope of the metric rectangle.
    bbox_lonlat: tuple[float, float, float, float]

    @property
    def width_m(self) -> float:
        return self.bounds_m[2] - self.bounds_m[0]

    @property
    def height_m(self) -> float:
        return self.bounds_m[3] - self.bounds_m[1]


def parse_gpx(data: bytes | str) -> Track:
    """Parse GPX content into a Track. Uses tracks, or routes if the file has no tracks."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    try:
        gpx = gpxpy.parse(data)
    except Exception as exc:  # gpxpy raises several exception types
        raise GpxError(f"Not a valid GPX file: {exc}") from exc

    segments: list[list[TrackPoint]] = []
    name: str | None = None
    for trk in gpx.tracks:
        name = name or trk.name
        for seg in trk.segments:
            segments.append([_point(p) for p in seg.points])
    if not any(segments):
        for rte in gpx.routes:
            name = name or rte.name
            segments.append([_point(p) for p in rte.points])

    segments = [s for s in segments if s]
    if not segments:
        raise GpxError("The GPX file contains no track or route points.")
    return Track(name=name or gpx.name, segments=segments)


def _point(p: gpxpy.gpx.GPXTrackPoint | gpxpy.gpx.GPXRoutePoint) -> TrackPoint:
    ele = p.elevation
    if ele is not None and not (MIN_VALID_ELE_M <= ele <= MAX_VALID_ELE_M):
        ele = None
    return TrackPoint(lat=float(p.latitude), lon=float(p.longitude), ele=ele)


def clean_track(track: Track, spike_m: float = 500.0) -> Track:
    """Drop invalid coordinates, consecutive duplicates and isolated GPS spikes.

    A spike is a single point far (> spike_m) from both neighbours while the
    neighbours themselves are close to each other.
    """
    cleaned: list[list[TrackPoint]] = []
    for seg in track.segments:
        pts = [p for p in seg if -90 <= p.lat <= 90 and -180 <= p.lon <= 180]
        pts = [
            p
            for i, p in enumerate(pts)
            if i == 0 or (p.lat, p.lon) != (pts[i - 1].lat, pts[i - 1].lon)
        ]

        kept: list[TrackPoint] = []
        for i, p in enumerate(pts):
            if kept and i + 1 < len(pts):
                prev, nxt = kept[-1], pts[i + 1]
                if (
                    haversine_m(prev.lat, prev.lon, p.lat, p.lon) > spike_m
                    and haversine_m(p.lat, p.lon, nxt.lat, nxt.lon) > spike_m
                    and haversine_m(prev.lat, prev.lon, nxt.lat, nxt.lon) < spike_m / 2
                ):
                    continue
            kept.append(p)
        if len(kept) >= 2:
            cleaned.append(kept)

    if not cleaned:
        raise GpxError("The GPX track needs at least two distinct points.")
    return Track(name=track.name, segments=cleaned)


def simplify_track(track: Track, tolerance_m: float) -> Track:
    """Douglas-Peucker simplification in a local metric projection."""
    if tolerance_m <= 0:
        return track
    lon0, lat0 = _center(track)
    fwd = to_crs(utm_crs_for(lon0, lat0))
    segments = []
    for seg in track.segments:
        x, y = fwd.transform([p.lon for p in seg], [p.lat for p in seg])
        keep = _douglas_peucker(np.column_stack([x, y]), tolerance_m)
        segments.append([p for p, k in zip(seg, keep, strict=True) if k])
    return Track(name=track.name, segments=segments)


def _douglas_peucker(xy: np.ndarray, tol: float) -> np.ndarray:
    """Boolean mask of the points kept by Douglas-Peucker (iterative, no recursion limit)."""
    n = len(xy)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        a, b = xy[start], xy[end]
        seg = b - a
        pts = xy[start + 1 : end] - a
        seg_len = np.hypot(*seg)
        if seg_len == 0:
            dist = np.hypot(pts[:, 0], pts[:, 1])
        else:
            dist = np.abs(seg[0] * pts[:, 1] - seg[1] * pts[:, 0]) / seg_len
        i = int(np.argmax(dist))
        if dist[i] > tol:
            mid = start + 1 + i
            keep[mid] = True
            stack.append((start, mid))
            stack.append((mid, end))
    return keep


def compute_stats(track: Track, climb_threshold_m: float = 5.0) -> TrackStats:
    """Distance and elevation stats.

    Ascent/descent use a hysteresis threshold so GPS elevation noise is not
    counted as climbing.
    """
    distance = 0.0
    ascent = descent = 0.0
    has_ele = False
    for seg in track.segments:
        for a, b in zip(seg, seg[1:], strict=False):
            distance += haversine_m(a.lat, a.lon, b.lat, b.lon)
        ref: float | None = None
        for p in seg:
            if p.ele is None:
                continue
            has_ele = True
            if ref is None:
                ref = p.ele
            elif p.ele - ref >= climb_threshold_m:
                ascent += p.ele - ref
                ref = p.ele
            elif ref - p.ele >= climb_threshold_m:
                descent += ref - p.ele
                ref = p.ele

    eles = [p.ele for p in track.points if p.ele is not None]
    return TrackStats(
        distance_m=distance,
        ascent_m=ascent if has_ele else None,
        descent_m=descent if has_ele else None,
        min_ele_m=min(eles) if eles else None,
        max_ele_m=max(eles) if eles else None,
        point_count=len(track.points),
    )


def compute_frame(track: Track, margin_pct: float = 10.0, min_size_m: float = 1_000.0) -> Frame:
    """Rectangular print area around the track.

    The margin is a percentage of the track's larger dimension, added on every
    side, so narrow routes still get some terrain around them.
    """
    lon0, lat0 = _center(track)
    crs = utm_crs_for(lon0, lat0)
    pts = track.points
    x, y = to_crs(crs).transform([p.lon for p in pts], [p.lat for p in pts])
    min_x, max_x, min_y, max_y = min(x), max(x), min(y), max(y)

    margin = max(max_x - min_x, max_y - min_y) * margin_pct / 100.0
    min_x, max_x, min_y, max_y = min_x - margin, max_x + margin, min_y - margin, max_y + margin

    min_x, max_x = _ensure_min_size(min_x, max_x, min_size_m)
    min_y, max_y = _ensure_min_size(min_y, max_y, min_size_m)
    bounds = (min_x, min_y, max_x, max_y)

    # Densify the rectangle edges so the lon/lat envelope covers the curved projection.
    t = np.linspace(0.0, 1.0, 21)
    ex = np.concatenate(
        [
            min_x + t * (max_x - min_x),
            np.full_like(t, max_x),
            max_x - t * (max_x - min_x),
            np.full_like(t, min_x),
        ]
    )
    ey = np.concatenate(
        [
            np.full_like(t, min_y),
            min_y + t * (max_y - min_y),
            np.full_like(t, max_y),
            max_y - t * (max_y - min_y),
        ]
    )
    lons, lats = from_crs(crs).transform(ex, ey)
    bbox = (float(min(lons)), float(min(lats)), float(max(lons)), float(max(lats)))

    return Frame(epsg=crs.to_epsg(), bounds_m=bounds, bbox_lonlat=bbox)


def _ensure_min_size(lo: float, hi: float, size: float) -> tuple[float, float]:
    if hi - lo >= size:
        return lo, hi
    mid = (lo + hi) / 2
    return mid - size / 2, mid + size / 2


def _center(track: Track) -> tuple[float, float]:
    pts = track.points
    lons = [p.lon for p in pts]
    lats = [p.lat for p in pts]
    return (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2


def segment_distances(track: Track) -> list[np.ndarray]:
    """Distance along the route (m) at each point, per segment.

    Distances continue from one segment to the next; the gap between two
    segments (e.g. a paused recording) is not counted.
    """
    out = []
    offset = 0.0
    for seg in track.segments:
        steps = [
            haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(seg, seg[1:], strict=False)
        ]
        d = offset + np.concatenate([[0.0], np.cumsum(steps)])
        out.append(d)
        offset = float(d[-1])
    return out


def track_length_m(track: Track) -> float:
    return float(segment_distances(track)[-1][-1])


def _interpolate(a: TrackPoint, b: TrackPoint, t: float) -> TrackPoint:
    ele = None if a.ele is None or b.ele is None else a.ele + t * (b.ele - a.ele)
    return TrackPoint(lat=a.lat + t * (b.lat - a.lat), lon=a.lon + t * (b.lon - a.lon), ele=ele)


def _point_at(seg: list[TrackPoint], d: np.ndarray, at: float) -> TrackPoint:
    i = int(np.clip(np.searchsorted(d, at) - 1, 0, len(seg) - 2))
    span = d[i + 1] - d[i]
    return _interpolate(seg[i], seg[i + 1], 0.0 if span == 0 else (at - d[i]) / span)


def slice_track(track: Track, start_m: float, end_m: float) -> Track:
    """Portion of the track between two distances along it; cut points are interpolated."""
    if end_m - start_m < 1.0:
        raise GpxError("The selected portion of the route is too short.")
    segments = []
    for seg, d in zip(track.segments, segment_distances(track), strict=True):
        if d[-1] < start_m or d[0] > end_m:
            continue
        lo, hi = max(start_m, d[0]), min(end_m, d[-1])
        inside = [p for p, di in zip(seg, d, strict=True) if lo < di < hi]
        part = [_point_at(seg, d, lo), *inside, _point_at(seg, d, hi)]
        if hi > lo:
            segments.append(part)
    if not segments:
        raise GpxError("The selected portion of the route is empty.")
    return Track(name=track.name, segments=segments)


def elevation_profile(track: Track, max_points: int = 600) -> dict:
    """Distance (km) and elevation (m) along the route, resampled evenly for a chart."""
    distances = segment_distances(track)
    d = np.concatenate(distances)
    if any(p.ele is None for p in track.points):
        return {"distance_km": [], "elevation_m": []}
    ele = np.array([p.ele for p in track.points], dtype=float)
    # np.interp needs increasing x: keep the first of repeated distances (gaps, duplicates).
    keep = np.concatenate([[True], np.diff(d) > 0])
    samples = np.linspace(0.0, d[-1], min(max_points, max(int(keep.sum()), 2)))
    return {
        "distance_km": np.round(samples / 1000, 4).tolist(),
        "elevation_m": np.round(np.interp(samples, d[keep], ele[keep]), 1).tolist(),
    }


def track_to_geojson(track: Track) -> dict:
    """GeoJSON Feature (MultiLineString) for map display."""
    return {
        "type": "Feature",
        "properties": {"name": track.name},
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [[[p.lon, p.lat] for p in seg] for seg in track.segments],
        },
    }


def load_gpx(data: bytes | str) -> Track:
    """Parse and clean a GPX file in one go."""
    return clean_track(parse_gpx(data))
