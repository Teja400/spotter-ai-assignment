"""Geometry helpers: distances and projecting points onto a polyline route.

All distances are in miles. Coordinates are (lat, lng) in decimal degrees.
"""

import math

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_MILES * c


def _project_onto_segment(point, seg_start, seg_end):
    """
    Project `point` onto the segment [seg_start, seg_end] using a local
    equirectangular (flat-earth) approximation - accurate enough for the
    short segments of a route polyline.

    Returns (projected_lat, projected_lng, fraction_along_segment) where
    fraction_along_segment is clamped to [0, 1].
    """
    lat0 = seg_start[0]
    # Scale longitude degrees by cos(latitude) so x/y are comparable "miles".
    cos_lat = math.cos(math.radians(lat0)) or 1e-9

    def to_xy(p):
        return (p[1] - seg_start[1]) * cos_lat, p[0] - seg_start[0]

    px, py = to_xy(point)
    ex, ey = to_xy(seg_end)

    seg_len_sq = ex * ex + ey * ey
    if seg_len_sq == 0:
        return seg_start[0], seg_start[1], 0.0

    t = (px * ex + py * ey) / seg_len_sq
    t = max(0.0, min(1.0, t))

    proj_x, proj_y = ex * t, ey * t
    proj_lng = seg_start[1] + proj_x / cos_lat
    proj_lat = seg_start[0] + proj_y

    return proj_lat, proj_lng, t


def build_cumulative_distances(route_points):
    """
    Given an ordered list of (lat, lng) route points, return a parallel list
    of cumulative distance-from-start (miles) at each point.
    """
    cumulative = [0.0]
    for i in range(1, len(route_points)):
        prev, curr = route_points[i - 1], route_points[i]
        cumulative.append(cumulative[-1] + haversine_miles(prev[0], prev[1], curr[0], curr[1]))
    return cumulative


def snap_point_to_route(point, route_points, cumulative_distances):
    """
    Find the closest point on the route polyline to `point`.

    Returns (distance_along_route_miles, offset_from_route_miles) - i.e. how
    far along the route the closest point is, and how far off-route `point`
    itself sits (perpendicular-ish distance to the nearest segment).
    """
    best_offset = math.inf
    best_distance_along = 0.0

    for i in range(1, len(route_points)):
        seg_start, seg_end = route_points[i - 1], route_points[i]

        proj_lat, proj_lng, t = _project_onto_segment(point, seg_start, seg_end)
        offset = haversine_miles(point[0], point[1], proj_lat, proj_lng)

        if offset < best_offset:
            best_offset = offset
            seg_start_dist = cumulative_distances[i - 1]
            seg_end_dist = cumulative_distances[i]
            best_distance_along = seg_start_dist + t * (seg_end_dist - seg_start_dist)

    return best_distance_along, best_offset
