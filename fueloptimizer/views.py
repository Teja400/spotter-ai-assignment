import json
import uuid

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from fueloptimizer.models import FuelStation as FuelStationModel
from fueloptimizer.services import stations as stations_service
from fueloptimizer.services.geocoding import (
    AddressNotFoundError,
    geocode_address,
    geocode_address_details,
)
from fueloptimizer.services.graphhopper import RouteNotFoundError
from fueloptimizer.services.optimizer import RouteInfeasibleError
from fueloptimizer.services.trip_planner import plan_trip


def _resolve_point(request, prefix: str) -> tuple[float, float]:
    """
    Resolve a (lat, lng) point for `prefix` ("start" or "end") from the
    request's query params.

    Prefers a full address (`{prefix}_address`), geocoding it via the
    Geoapify API. Falls back to raw `{prefix}_lat`/`{prefix}_lng` numeric
    params for backwards compatibility.
    """
    address = request.GET.get(f"{prefix}_address")
    if address:
        return geocode_address(address)

    return (
        float(request.GET[f"{prefix}_lat"]),
        float(request.GET[f"{prefix}_lng"]),
    )


@require_GET
def plan_route(request):
    """
    GET /fueloptimizer/plan/?start_address=..&end_address=..
    (or, for backwards compatibility:
     ?start_lat=..&start_lng=..&end_lat=..&end_lng=..)

    Resolves each address to coordinates via the Geoapify geocoding API (or
    uses the raw lat/lng query params if provided instead), fetches the
    driving route between them, and returns the route (for map rendering)
    plus the optimized list of fuel stops (station, gallons to buy, cost)
    and total trip fuel cost.
    """
    try:
        start = _resolve_point(request, "start")
        end = _resolve_point(request, "end")
    except (KeyError, ValueError):
        return JsonResponse(
            {
                "error": (
                    "Provide start_address & end_address (full address strings), "
                    "or start_lat, start_lng, end_lat, end_lng as numeric query params."
                )
            },
            status=400,
        )
    except AddressNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=422)

    try:
        result = plan_trip(start, end)
    except RouteNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    except RouteInfeasibleError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    except RuntimeError as exc:
        # e.g. GRAPHHOPPER_API_KEY / API_KEY not configured yet.
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse(result)


@csrf_exempt
@require_POST
def register_station(request):
    """
    POST /fueloptimizer/stations/register/
    Body (JSON): {
        "address": "123 Main St, Springfield, IL",   (required)
        "price_per_gallon": 3.45,                    (required)
        "name": "Example Fuel Stop",                 (optional)
        "station_id": "MY-CUSTOM-ID"                 (optional; auto-generated if omitted)
    }

    Geocodes the given address (via the Geoapify API) into lat/lng, then
    saves a new FuelStation row to the DB with those coordinates. The
    station is immediately available to future /plan/ requests - no
    server restart required.
    """
    try:
        payload = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Request body must be valid JSON."}, status=400)

    address = (payload.get("address") or "").strip()
    if not address:
        return JsonResponse({"error": "'address' is required."}, status=400)

    price_raw = payload.get("price_per_gallon")
    if price_raw is None:
        return JsonResponse({"error": "'price_per_gallon' is required."}, status=400)
    try:
        price_per_gallon = float(price_raw)
    except (TypeError, ValueError):
        return JsonResponse({"error": "'price_per_gallon' must be a number."}, status=400)
    if price_per_gallon <= 0:
        return JsonResponse({"error": "'price_per_gallon' must be positive."}, status=400)

    station_id = (payload.get("station_id") or "").strip() or f"MANUAL-{uuid.uuid4().hex[:12]}"
    name = (payload.get("name") or "").strip() or address

    if FuelStationModel.objects.filter(station_id=station_id).exists():
        return JsonResponse(
            {"error": f"A station with station_id '{station_id}' already exists."},
            status=409,
        )

    try:
        details = geocode_address_details(address)
    except AddressNotFoundError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    except RuntimeError as exc:
        # e.g. API_KEY not configured yet.
        return JsonResponse({"error": str(exc)}, status=500)

    station = FuelStationModel.objects.create(
        station_id=station_id,
        name=name,
        city=details["city"],
        state=details["state"],
        price_per_gallon=price_per_gallon,
        lat=details["lat"],
        lng=details["lng"],
    )

    # Invalidate the in-process station cache so this new station is
    # picked up by the very next /plan/ request.
    stations_service.invalidate_cache()

    return JsonResponse(
        {
            "station_id": station.station_id,
            "name": station.name,
            "city": station.city,
            "state": station.state,
            "price_per_gallon": station.price_per_gallon,
            "lat": station.lat,
            "lng": station.lng,
            "resolved_address": details["formatted"],
        },
        status=201,
    )
