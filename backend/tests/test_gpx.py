from pathlib import Path

import pytest

from app.geo import haversine_m
from app.gpx import (
    GpxError,
    Track,
    TrackPoint,
    clean_track,
    compute_frame,
    compute_stats,
    elevation_profile,
    load_gpx,
    parse_gpx,
    simplify_track,
    slice_track,
    track_length_m,
    track_to_geojson,
)

FIXTURES = Path(__file__).parent / "fixtures"


def gpx_doc(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        f"{body}</gpx>"
    )


def trkpts(points: list[tuple[float, float, float | None]]) -> str:
    out = []
    for lat, lon, ele in points:
        ele_tag = f"<ele>{ele}</ele>" if ele is not None else ""
        out.append(f'<trkpt lat="{lat}" lon="{lon}">{ele_tag}</trkpt>')
    return "".join(out)


@pytest.fixture
def alps_loop() -> Track:
    return load_gpx((FIXTURES / "alps_loop.gpx").read_bytes())


def test_parse_fixture(alps_loop: Track) -> None:
    assert alps_loop.name == "Synthetic Chamonix loop"
    assert len(alps_loop.segments) == 1
    assert len(alps_loop.points) == 401


def test_parse_routes_when_no_tracks() -> None:
    doc = gpx_doc(
        "<rte><name>Planned</name>"
        '<rtept lat="45.0" lon="6.0"/><rtept lat="45.01" lon="6.01"/></rte>'
    )
    track = parse_gpx(doc)
    assert track.name == "Planned"
    assert len(track.points) == 2
    assert track.points[0].ele is None


def test_parse_keeps_segments_separate() -> None:
    doc = gpx_doc(
        "<trk>"
        f"<trkseg>{trkpts([(45.0, 6.0, 1000), (45.001, 6.0, 1001)])}</trkseg>"
        f"<trkseg>{trkpts([(45.1, 6.1, 1100), (45.101, 6.1, 1101)])}</trkseg>"
        "</trk>"
    )
    track = parse_gpx(doc)
    assert [len(s) for s in track.segments] == [2, 2]


@pytest.mark.parametrize(
    "data",
    [b"not xml at all", gpx_doc(""), gpx_doc('<wpt lat="45" lon="6"/>')],
)
def test_parse_rejects_unusable_files(data: bytes | str) -> None:
    with pytest.raises(GpxError):
        parse_gpx(data)


def test_parse_drops_absurd_elevation() -> None:
    track = parse_gpx(gpx_doc(f"<trk><trkseg>{trkpts([(45.0, 6.0, 99999)])}</trkseg></trk>"))
    assert track.points[0].ele is None


def test_clean_removes_duplicates_and_spikes() -> None:
    pts = [
        TrackPoint(45.0, 6.0),
        TrackPoint(45.0, 6.0),  # duplicate
        TrackPoint(45.0001, 6.0),
        TrackPoint(45.5, 6.5),  # ~65 km spike
        TrackPoint(45.0002, 6.0),
        TrackPoint(45.0003, 6.0),
    ]
    cleaned = clean_track(Track(name=None, segments=[pts]))
    assert [(p.lat, p.lon) for p in cleaned.points] == [
        (45.0, 6.0),
        (45.0001, 6.0),
        (45.0002, 6.0),
        (45.0003, 6.0),
    ]


def test_clean_keeps_real_long_jumps() -> None:
    # A long straight leg (e.g. a ferry) is not a spike: the neighbours are far apart too.
    pts = [TrackPoint(45.0, 6.0), TrackPoint(45.1, 6.0), TrackPoint(45.2, 6.0)]
    assert len(clean_track(Track(name=None, segments=[pts])).points) == 3


def test_clean_rejects_single_point() -> None:
    with pytest.raises(GpxError):
        clean_track(Track(name=None, segments=[[TrackPoint(45.0, 6.0)] * 3]))


def test_stats(alps_loop: Track) -> None:
    stats = compute_stats(alps_loop)
    # Ellipse with semi-axes ~2.0 km (lat) and ~2.5 km (lon): perimeter ~14.1 km.
    assert stats.distance_m == pytest.approx(14_100, rel=0.02)
    # Elevation is a sinusoid from ~1050 m to ~2150 m: one climb and one descent of ~1100 m,
    # with the 1.5 m GPS noise filtered out by the hysteresis.
    assert stats.ascent_m == pytest.approx(1_100, abs=30)
    assert stats.descent_m == pytest.approx(1_100, abs=30)
    assert stats.min_ele_m == pytest.approx(1_050, abs=10)
    assert stats.max_ele_m == pytest.approx(2_150, abs=10)
    assert stats.point_count == 401


def test_stats_without_elevation() -> None:
    track = Track(name=None, segments=[[TrackPoint(45.0, 6.0), TrackPoint(45.01, 6.0)]])
    stats = compute_stats(track)
    assert stats.ascent_m is None and stats.min_ele_m is None
    assert stats.distance_m == pytest.approx(haversine_m(45.0, 6.0, 45.01, 6.0))


def test_simplify_reduces_points_within_tolerance(alps_loop: Track) -> None:
    simplified = simplify_track(alps_loop, tolerance_m=5)
    assert 10 < len(simplified.points) < len(alps_loop.points) / 2
    # Endpoints and elevations are preserved on kept points.
    assert simplified.points[0] == alps_loop.points[0]
    assert simplified.points[-1] == alps_loop.points[-1]
    # Simplification only shortens the path slightly.
    ratio = compute_stats(simplified).distance_m / compute_stats(alps_loop).distance_m
    assert 0.99 < ratio <= 1.0


def test_frame_contains_track_with_margin(alps_loop: Track) -> None:
    frame = compute_frame(alps_loop, margin_pct=10)
    assert frame.epsg == 32632  # Chamonix is in UTM zone 32N
    # Track extent ~5.0 x 4.0 km, plus 10 % of 5.0 km on each side.
    assert frame.width_m == pytest.approx(5_000 + 2 * 500, rel=0.02)
    assert frame.height_m == pytest.approx(4_000 + 2 * 500, rel=0.02)
    west, south, east, north = frame.bbox_lonlat
    for p in alps_loop.points:
        assert west < p.lon < east and south < p.lat < north


def test_frame_minimum_size() -> None:
    track = Track(name=None, segments=[[TrackPoint(45.0, 6.0), TrackPoint(45.0001, 6.0)]])
    frame = compute_frame(track, margin_pct=0, min_size_m=1_000)
    assert frame.width_m == pytest.approx(1_000)
    assert frame.height_m == pytest.approx(1_000)


def test_geojson(alps_loop: Track) -> None:
    feature = track_to_geojson(alps_loop)
    coords = feature["geometry"]["coordinates"]
    assert feature["geometry"]["type"] == "MultiLineString"
    assert len(coords) == 1 and len(coords[0]) == 401
    first = alps_loop.points[0]
    assert coords[0][0] == [first.lon, first.lat]


def straight(n: int = 11, lat0: float = 45.0, step: float = 0.001) -> list[TrackPoint]:
    """Points due north, ~111 m apart, climbing 10 m per point."""
    return [TrackPoint(lat0 + i * step, 6.0, 1000.0 + 10 * i) for i in range(n)]


def test_slice_interpolates_the_cut_points() -> None:
    track = Track(name="line", segments=[straight()])
    total = track_length_m(track)
    assert total == pytest.approx(1112, rel=0.01)

    part = slice_track(track, total * 0.25, total * 0.75)
    assert compute_stats(part).distance_m == pytest.approx(total / 2, rel=1e-6)
    first, last = part.points[0], part.points[-1]
    assert first.lat == pytest.approx(45.0025)
    assert first.ele == pytest.approx(1025)
    assert last.lat == pytest.approx(45.0075)
    assert last.ele == pytest.approx(1075)
    assert part.name == "line"


def test_slice_across_segments_ignores_the_gap() -> None:
    # Two 1.1 km segments with a 10 km jump between them (e.g. a paused recording).
    track = Track(name=None, segments=[straight(), straight(lat0=45.1)])
    total = track_length_m(track)
    assert total == pytest.approx(2 * 1112, rel=0.01)

    part = slice_track(track, total * 0.4, total * 0.6)
    assert len(part.segments) == 2
    assert compute_stats(part).distance_m == pytest.approx(total * 0.2, rel=1e-6)

    only_second = slice_track(track, total * 0.6, total)
    assert len(only_second.segments) == 1
    assert only_second.points[0].lat > 45.1


def test_slice_rejects_empty_portions() -> None:
    track = Track(name=None, segments=[straight()])
    with pytest.raises(GpxError):
        slice_track(track, 500, 500.5)
    with pytest.raises(GpxError):
        slice_track(track, 5_000, 6_000)


def test_climb_of_a_real_ride(alps_loop: Track) -> None:
    profile = elevation_profile(alps_loop, max_points=200)
    assert len(profile["distance_km"]) == 200
    assert profile["distance_km"][-1] == pytest.approx(track_length_m(alps_loop) / 1000, abs=1e-4)
    assert max(profile["elevation_m"]) == pytest.approx(2150, abs=10)


def test_profile_without_elevation() -> None:
    track = Track(name=None, segments=[[TrackPoint(45.0, 6.0), TrackPoint(45.01, 6.0)]])
    assert elevation_profile(track) == {"distance_km": [], "elevation_m": []}
