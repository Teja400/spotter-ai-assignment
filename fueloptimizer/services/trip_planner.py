"""End-to-end pipeline: route -> candidate stations -> optimized fuel stops."""

import math

from fueloptimizer.constants import MAX_STATION_OFFSET_MILES, ROUTE_BBOX_BUFFER_MILES
from fueloptimizer.services import graphhopper
from fueloptimizer.services.geo import build_cumulative_distances, snap_point_to_route
from fueloptimizer.services.optimizer import RouteStation, optimize_fuel_stops
from fueloptimizer.services.stations import load_stations


def plan_trip(start: tuple[float, float], end: tuple[float, float]) -> dict:
    """
    Fetch the route between start/end, snap known fuel stations onto it,
    and return the optimized fuel-stop plan alongside the route itself.
    """
    route_points = graphhopper.get_route(start, end)
    cumulative = build_cumulative_distances(route_points)
    total_distance = cumulative[-1]
    route_lats = [lat for lat, _ in route_points]
    route_lngs = [lng for _, lng in route_points]
    min_lat, max_lat = min(route_lats), max(route_lats)
    min_lng, max_lng = min(route_lngs), max(route_lngs)

    miles_per_degree_lat = 69.0
    # Use the most extreme latitude (largest |lat|, smallest cos) reached by
    # the route to compute the longitude buffer. Using a single average
    # center latitude would under-estimate the buffer near the pole-ward end
    # of routes that span a wide latitude range, incorrectly excluding
    # stations that are actually within MAX_STATION_OFFSET_MILES of the route.
    extreme_lat = max(abs(min_lat), abs(max_lat))
    miles_per_degree_lng = max(69.0 * math.cos(math.radians(extreme_lat)), 1e-6)
    lat_buffer_deg = ROUTE_BBOX_BUFFER_MILES / miles_per_degree_lat
    lng_buffer_deg = ROUTE_BBOX_BUFFER_MILES / miles_per_degree_lng

    route_stations: list[RouteStation] = []
    for station in load_stations():
        if not (
            (min_lat - lat_buffer_deg) <= station.lat <= (max_lat + lat_buffer_deg)
            and (min_lng - lng_buffer_deg) <= station.lng <= (max_lng + lng_buffer_deg)
        ):
            continue
        distance_along, offset = snap_point_to_route(
            (station.lat, station.lng), route_points, cumulative
        )
        if offset <= MAX_STATION_OFFSET_MILES:
            route_stations.append(
                RouteStation(
                    station_id=station.station_id,
                    name=station.name,
                    lat=station.lat,
                    lng=station.lng,
                    price_per_gallon=station.price_per_gallon,
                    distance_along_route=distance_along,
                )
            )

    stops, total_cost = optimize_fuel_stops(route_stations, total_distance)

    return {
        "route": [{"lat": lat, "lng": lng} for lat, lng in route_points],
        "total_distance_miles": round(total_distance, 2),
        "total_fuel_cost": round(total_cost, 2),
        "fuel_stops": [
            {
                "station_id": stop.station.station_id,
                "name": stop.station.name,
                "lat": stop.station.lat,
                "lng": stop.station.lng,
                "distance_along_route_miles": round(stop.station.distance_along_route, 2),
                "price_per_gallon": stop.station.price_per_gallon,
                "gallons_purchased": round(stop.gallons_purchased, 2),
                "cost": round(stop.cost, 2),
            }
            for stop in stops
        ],
    }
