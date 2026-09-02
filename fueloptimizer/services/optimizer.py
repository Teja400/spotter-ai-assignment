"""
Greedy cost-minimizing fuel-stop optimizer.

Problem: given a route of total length D, a vehicle with tank capacity
covering MAX_RANGE_MILES per full tank, and a set of fuel stations at known
distances along the route (each with a price per gallon), choose where to
stop and how much to buy so the vehicle never runs out of fuel and total
fuel cost is minimized. Partial fills are allowed (buy any amount up to a
full tank at each stop).

Algorithm ("next cheaper station" greedy - optimal for the continuous
partial-fill model):
    Walk the route left to right, tracking current fuel (in miles of range).
    At the current position, look ahead within MAX_RANGE_MILES for the next
    station whose price is lower than the current station's:
      - If one exists at distance X: buy just enough fuel to reach it
        (topping up only as much as needed), since anything bought here
        that isn't needed to reach the cheaper station is wasted cost.
      - If none exists in range: fill the tank completely here, since this
        is the cheapest price available before the tank would run dry.
    Repeat from station to station until the destination is reached.

This mirrors the classic "buy fuel greedily against the next lower price"
strategy used for line/route refueling cost problems.
"""

from dataclasses import dataclass

from fueloptimizer.constants import MAX_RANGE_MILES, MILES_PER_GALLON


@dataclass(frozen=True)
class RouteStation:
    """A fuel station already snapped onto the route."""
    station_id: str
    name: str
    lat: float
    lng: float
    price_per_gallon: float
    distance_along_route: float  # miles from the trip start


@dataclass(frozen=True)
class FuelStop:
    station: RouteStation
    gallons_purchased: float
    cost: float


class RouteInfeasibleError(Exception):
    """Raised when the gap between two consecutive mandatory points exceeds MAX_RANGE_MILES."""


def optimize_fuel_stops(
    stations: list[RouteStation],
    total_distance_miles: float,
    starting_fuel_miles: float | None = None,
) -> tuple[list[FuelStop], float]:
    """
    Choose fuel stops and quantities to minimize total cost for a trip of
    `total_distance_miles`, given candidate `stations` already sorted (or
    not - this function sorts them) by distance_along_route.

    `starting_fuel_miles` defaults to a full tank (MAX_RANGE_MILES).

    Returns (list_of_FuelStop_in_order, total_cost). Stations not selected
    are simply omitted from the result.
    """
    ordered = sorted(stations, key=lambda s: s.distance_along_route)

    # Validate feasibility: no gap (including start->first and last->end)
    # may exceed the vehicle's max range.
    checkpoints = [0.0] + [s.distance_along_route for s in ordered] + [total_distance_miles]
    for prev, curr in zip(checkpoints, checkpoints[1:]):
        if curr - prev > MAX_RANGE_MILES + 1e-9:
            raise RouteInfeasibleError(
                f"Gap of {curr - prev:.1f} miles exceeds the {MAX_RANGE_MILES:.0f}-mile "
                f"max range with no station in between."
            )

    fuel_miles = MAX_RANGE_MILES if starting_fuel_miles is None else starting_fuel_miles
    position = 0.0
    stops: list[FuelStop] = []
    total_cost = 0.0

    for i, station in enumerate(ordered):
        # Fuel remaining (in miles of range) when we arrive at this station.
        fuel_miles -= station.distance_along_route - position
        position = station.distance_along_route

        # Find the next station (or the destination) that is cheaper than
        # this one, within one tank's range of this station.
        cheaper_distance = None
        for later in ordered[i + 1:]:
            if later.distance_along_route - position > MAX_RANGE_MILES:
                break
            if later.price_per_gallon < station.price_per_gallon:
                cheaper_distance = later.distance_along_route
                break

        if cheaper_distance is None and total_distance_miles - position <= MAX_RANGE_MILES:
            # Can reach the destination itself without a cheaper stop in between.
            cheaper_distance = total_distance_miles

        if cheaper_distance is not None:
            # Buy only enough to reach that cheaper point (or the end).
            needed_miles = (cheaper_distance - position) - fuel_miles
        else:
            # No cheaper option in range - fill up completely here.
            needed_miles = MAX_RANGE_MILES - fuel_miles

        needed_miles = max(0.0, needed_miles)

        if needed_miles > 0:
            gallons = needed_miles / MILES_PER_GALLON
            cost = gallons * station.price_per_gallon
            stops.append(FuelStop(station=station, gallons_purchased=gallons, cost=cost))
            total_cost += cost
            fuel_miles += needed_miles

    return stops, total_cost
