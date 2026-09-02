"""Thin client for the GraphHopper Routing API."""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPHHOPPER_API_KEY = os.environ.get("GRAPHHOPPER_API_KEY")
GRAPHHOPPER_URL = "https://graphhopper.com/api/1/route"
REQUEST_TIMEOUT = 15


class RouteNotFoundError(Exception):
    """Raised when GraphHopper cannot find a route between the two points."""


def get_route(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
    """
    Fetch a driving route between `start` and `end` (lat, lng tuples).

    Returns an ordered list of (lat, lng) points describing the route
    polyline, in the order traveled from start to end.
    """
    if not GRAPHHOPPER_API_KEY:
        raise RuntimeError("GRAPHHOPPER_API_KEY is not set. Add it to your .env file.")

    params = {
        "point": [f"{start[0]},{start[1]}", f"{end[0]},{end[1]}"],
        "vehicle": "car",
        "points_encoded": "false",
        "key": GRAPHHOPPER_API_KEY,
    }

    response = requests.get(GRAPHHOPPER_URL, params=params, timeout=REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise RouteNotFoundError(
            f"GraphHopper returned HTTP {response.status_code}: {response.text[:300]}"
        )

    payload = response.json()
    paths = payload.get("paths")

    if not paths:
        raise RouteNotFoundError("GraphHopper returned no paths for the given start/end points.")

    coordinates = paths[0]["points"]["coordinates"]  # [[lng, lat], ...]
    return [(lat, lng) for lng, lat in coordinates]
