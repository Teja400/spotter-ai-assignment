"""Hardcoded vehicle constants for the fuel-stop optimizer.

These describe a single "reference" vehicle for the assessment. They are
plain constants (not per-request parameters) as agreed - if this needs to
become configurable per trip later, turn these into request parameters
instead of changing the optimizer logic.
"""

# Maximum distance (miles) the vehicle can travel on a full tank.
MAX_RANGE_MILES = 500.0

# Fuel tank capacity in gallons.
TANK_CAPACITY_GALLONS = 50.0

# Derived: miles covered per gallon of fuel.
MILES_PER_GALLON = MAX_RANGE_MILES / TANK_CAPACITY_GALLONS

# How far (miles) a station may sit from the route polyline and still be
# considered "on route". Stations farther than this are ignored entirely.
MAX_STATION_OFFSET_MILES = 10.0

# Coarse prefilter buffer around the route bounding box. Stations outside this
# box are skipped before exact snap-to-route distance is computed.
ROUTE_BBOX_BUFFER_MILES = 10.0
