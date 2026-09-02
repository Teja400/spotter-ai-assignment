"""Thin client for the Geoapify Geocoding API - turns a free-text address
into (lat, lng) coordinates that can then be fed into the GraphHopper
routing client (see graphhopper.get_route).
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

GEOCODE_API_KEY = os.environ.get("API_KEY")
GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
REQUEST_TIMEOUT = 15


class AddressNotFoundError(Exception):
    """Raised when the geocoding API cannot resolve the given address."""


def geocode_address_details(address: str) -> dict:
    """
    Resolve a free-text address (e.g. "1600 Pennsylvania Ave, Washington, DC")
    into its full geocoding result: lat/lng plus city, state and the
    API's formatted address.

    Raises AddressNotFoundError if the address can't be resolved, or
    RuntimeError if the API key is not configured.
    """
    if not GEOCODE_API_KEY:
        raise RuntimeError("API_KEY is not set. Add it to your .env file.")

    if not address or not address.strip():
        raise AddressNotFoundError("Address must not be empty.")

    params = {
        "text": address,
        "format": "json",
        "limit": 1,
        "apiKey": GEOCODE_API_KEY,
    }

    response = requests.get(GEOCODE_URL, params=params, timeout=REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise AddressNotFoundError(
            f"Geocoding API returned HTTP {response.status_code} for address "
            f"'{address}': {response.text[:300]}"
        )

    payload = response.json()
    results = payload.get("results")

    if not results:
        raise AddressNotFoundError(f"No coordinates found for address '{address}'.")

    best = results[0]
    lat, lon = best.get("lat"), best.get("lon")

    if lat is None or lon is None:
        raise AddressNotFoundError(f"No coordinates found for address '{address}'.")

    return {
        "lat": float(lat),
        "lng": float(lon),
        "city": best.get("city") or "",
        "state": best.get("state_code") or best.get("state") or "",
        "formatted": best.get("formatted") or address,
    }


def geocode_address(address: str) -> tuple[float, float]:
    """
    Resolve a free-text address (e.g. "1600 Pennsylvania Ave, Washington, DC")
    into (lat, lng) decimal-degree coordinates.

    Raises AddressNotFoundError if the address can't be resolved, or
    RuntimeError if the API key is not configured.
    """
    details = geocode_address_details(address)
    return details["lat"], details["lng"]
