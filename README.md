# Fuel Route Optimizer

A Django API that, given a start and finish location anywhere in the USA, returns:
- the driving route (for map rendering),
- the **cost-optimal set of fuel stops** along that route, and
- the **total fuel cost** for the trip.

The core goal: given a vehicle with a 500-mile
range and a fixed fuel-economy of 10 mpg, figure out *where* to stop for fuel and
*how much* to buy at each stop so the total cost is minimized, using a real
dataset of fuel prices at truck stops across the US.

---

## Table of contents

- [How it works (high level)](#how-it-works-high-level)
- [The optimization algorithm](#the-optimization-algorithm)
- [Project structure](#project-structure)
- [Setup](#setup)
- [Environment variables](#environment-variables)
- [Running the server](#running-the-server)
- [API reference](#api-reference)
- [Example request/response](#example-requestresponse)
- [Data pipeline (fuel price dataset -> DB)](#data-pipeline-fuel-price-dataset---db)
- [Design decisions & assumptions](#design-decisions--assumptions)
- [External APIs used](#external-apis-used)

---

## How it works (high level)

```
start_address, end_address (query params)
        |
        v
 [1] Geocode both addresses -> (lat, lng)         (Geoapify Geocoding API)
        |
        v
 [2] Fetch the driving route between them          (GraphHopper Routing API, 1 call)
        |
        v
 [3] Load fuel stations from the DB
        |
        v
 [4] Keep only stations within ~10 miles of the route
     (fast bounding-box prefilter, then exact distance check)
        |
        v
 [5] Run the greedy fuel-stop optimizer
     (decide where to stop and how much to buy)
        |
        v
 [6] Return JSON: route, total distance, total fuel cost, fuel stops
```

Only **one** call is made to the routing API per request. Geocoding is a separate, lightweight API and only runs
when addresses (not raw coordinates) are provided.

## The optimization algorithm

File: [`fueloptimizer/services/optimizer.py`](fueloptimizer/services/optimizer.py)

This is the classic **"buy fuel greedily against the next cheaper price"**
strategy for the continuous partial-fill refueling problem:

1. Walk the route from start to finish, tracking how much fuel (in miles of
   remaining range) the vehicle has.
2. Before making a decision at any given station, feasibility is validated: if
   any gap between consecutive mandatory points (start -> first station ->
   ... -> destination) exceeds the 500-mile range, the trip is infeasible and
   a `422` is returned - there's no way to make it without a station in
   between.
3. At each station, look ahead (within the 500-mile range) for the next
   station that is **cheaper**:
   - **If one exists**, buy only enough fuel to reach it - buying more than
     that would mean paying today's (higher) price for fuel you could have
     bought cheaper later.
   - **If none exists** within range, fill the tank completely here, since
     this is the cheapest price available before the tank would run dry.
4. Repeat until the destination is reached.

This greedy rule is provably optimal for the "continuous, partial fills
allowed" version of the problem (as opposed to the classic discrete
"gas station" problem where you can only buy in fixed increments).

**Vehicle constants** (see [`fueloptimizer/constants.py`](fueloptimizer/constants.py)):

| Constant | Value | Meaning |
|---|---|---|
| `MAX_RANGE_MILES` | 500 | Max distance on a full tank |
| `TANK_CAPACITY_GALLONS` | 50 | Tank size |
| `MILES_PER_GALLON` | 10 | Fuel economy |
| `MAX_STATION_OFFSET_MILES` | 10 | Max distance a station may sit from the route and still count as "on route" |

### Finding stations near the route (efficiently)

A route from GraphHopper comes back as a polyline of a few thousand
`(lat, lng)` points. Naively checking every one of ~1,700+ fuel stations
against every route segment would be `O(stations x route_segments)` - slow.

Instead ([`fueloptimizer/services/trip_planner.py`](fueloptimizer/services/trip_planner.py)):

1. **Coarse prefilter:** build a bounding box around the whole route, expanded
   by a safety buffer (`ROUTE_BBOX_BUFFER_MILES`), and instantly discard any
   station outside it (a cheap lat/lng range check - no trig, no distance
   math). The buffer's miles-to-degrees conversion uses the *most extreme*
   latitude the route reaches (not an average), so the box is never
   under-sized near the pole-ward end of long north-south routes.
2. **Exact check (survivors only):** for stations inside the box, compute the
   true shortest distance from the station to the route polyline (project the
   point onto each route segment, take the minimum Haversine distance), and
   keep it only if that's within `MAX_STATION_OFFSET_MILES`.

This prefilter reduced measured planner latency by roughly **10x** in local
benchmarking, since only a small fraction of stations ever need the expensive
exact check.

## Project structure

```
djangoproject/              # Django project shell (settings, URLs, WSGI/ASGI)
fueloptimizer/               # The app - all business logic lives here
    models.py                 # FuelStation DB model
    views.py                  # plan_route view (the one API endpoint)
    urls.py
    constants.py               # Vehicle/tuning constants
    migrations/
    services/
        geocoding.py            # Geoapify: address -> (lat, lng)
        graphhopper.py          # GraphHopper: (lat,lng) x2 -> route polyline
        geo.py                  # Haversine distance, point-to-polyline projection
        stations.py             # Loads FuelStation rows from the DB
        optimizer.py            # The greedy fuel-stop algorithm
        trip_planner.py         # Wires the above into one pipeline
manage.py
db.sqlite3                   # SQLite DB (station data lives here, not in CSV)
fuel_prices_resolved.csv     # Geocoded fuel price dataset (see data pipeline below)
```

## Setup

Requires Python 3.13+ and [uv](https://github.com/astral-sh/uv) (or plain pip).

```powershell
# Install dependencies
uv sync
# --- or, with plain pip ---
pip install -e .
```

Apply database migrations (creates the `FuelStation` table, among others):

```powershell
python manage.py migrate
```

## Environment variables

Create a `.env` file in the project root (never committed):

```
API_KEY=<your Geoapify API key>            # https://myprojects.geoapify.com/
GRAPHHOPPER_API_KEY=<your GraphHopper key>  # https://www.graphhopper.com/
```

Both have free tiers that are more than sufficient for testing this project.

## Running the server

```powershell
python manage.py runserver
```

The API is now available at `http://127.0.0.1:8000/fueloptimizer/plan/`.

## API reference

### `GET /fueloptimizer/plan/`

**Preferred - full addresses** (geocoded automatically):

| Param | Type | Description |
|---|---|---|
| `start_address` | string | Free-text starting address, e.g. `"Dallas, TX"` |
| `end_address` | string | Free-text destination address |

**Alternative - raw coordinates** (skips geocoding):

| Param | Type | Description |
|---|---|---|
| `start_lat`, `start_lng` | float | Starting point coordinates |
| `end_lat`, `end_lng` | float | Destination coordinates |

**Response `200`:**

```json
{
  "route": [{"lat": 31.76, "lng": -106.49}, ...],
  "total_distance_miles": 725.09,
  "total_fuel_cost": 62.09,
  "fuel_stops": [
    {
      "station_id": "71079",
      "name": "DK",
      "lat": 31.7942502,
      "lng": -106.4610125,
      "distance_along_route_miles": 2.12,
      "price_per_gallon": 2.699,
      "gallons_purchased": 0.21,
      "cost": 0.57
    }
  ]
}
```

**Error responses:**

| Status | Meaning |
|---|---|
| `400` | Missing/invalid address or coordinate params |
| `422` | Address couldn't be geocoded, or the trip is infeasible (a gap between mandatory points exceeds 500 miles with no station in between) |
| `502` | The routing API couldn't find a route between the two points |
| `500` | A required API key is not configured |

### `POST /fueloptimizer/stations/register/`

Registers a new fuel stop directly in the DB - give it an address and a
price, and it geocodes the address into lat/lng coordinates for you. The
new station is available to `/plan/` immediately (no server restart
needed - the in-process station cache is invalidated automatically).

**Request body (JSON):**

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes | Free-text address to geocode, e.g. `"123 Main St, Springfield, IL"` |
| `price_per_gallon` | float | yes | Must be > 0 |
| `name` | string | no | Defaults to the given address |
| `station_id` | string | no | Defaults to an auto-generated `MANUAL-xxxxxxxxxxxx` id |

**Response `201`:**

```json
{
  "station_id": "MANUAL-e320c3b0092e",
  "name": "Example Fuel Stop",
  "city": "Springfield",
  "state": "IL",
  "price_per_gallon": 3.29,
  "lat": 39.798,
  "lng": -89.644,
  "resolved_address": "123 Main St, Springfield, IL 62701, United States of America"
}
```

**Error responses:**

| Status | Meaning |
|---|---|
| `400` | Missing/invalid `address` or `price_per_gallon` |
| `409` | A station with the given `station_id` already exists |
| `422` | Address couldn't be geocoded |
| `500` | A required API key is not configured |

Example with `curl`:

```powershell
curl -X POST "http://127.0.0.1:8000/fueloptimizer/stations/register/" `
  -H "Content-Type: application/json" `
  -d '{\"address\": \"123 Main St, Springfield, IL\", \"price_per_gallon\": 3.29}'
```



Using `curl`:

```powershell
curl "http://127.0.0.1:8000/fueloptimizer/plan/?start_address=El%20Paso,%20TX&end_address=Oklahoma%20City,%20OK"
```

Or in Postman: `GET` request to
`http://127.0.0.1:8000/fueloptimizer/plan/` with query params
`start_address` and `end_address`.

A route longer than 500 miles (like El Paso -> Oklahoma City, ~725 miles) is
the best demo case - it forces the algorithm to choose multiple fuel stops,
as opposed to a short route (e.g. Dallas -> Oklahoma City, ~206 miles) which
correctly returns zero stops since a full tank covers the whole trip.

## Data pipeline (fuel price dataset -> DB)

The provided fuel-price CSV only has city/state, not coordinates - it needs
to be geocoded before it's useful for route-snapping. This happened in three
stages (all under `utils/`, kept separate from the running app):

1. **`data_cleaning.py`** - cleans the raw CSV (duplicate
   station records, records with subtle differences).
2. **`resolve_address.py`** - geocodes each station's name/city/state via the
   Geoapify Geocoding API into precise coordinates, writing
   `utils/fuel_prices_resolved.csv`. This is incremental/resumable (skips rows
   already resolved) since the free API tier has a daily quota.
3. **`load_stations_to_db.py`** *(local-only, not committed)* - syncs the
   resolved CSV into the `FuelStation` DB table (`update_or_create`, safe to
   re-run any time the CSV gains more rows).

**`db.sqlite3` ships pre-seeded** with the full geocoded dataset (2,366+
stations across 16 states), so a fresh `git clone` + `migrate` (which just
applies schema, not data) works out of the box - no need to run any of the
pipeline scripts above unless you want to regenerate/expand the dataset
yourself. The intermediate CSVs are gitignored since they're just build
artifacts of that one-time pipeline.

**Important:** the running application never reads the CSV directly - only
the DB (`fueloptimizer/services/stations.py`). The CSV/geocoding scripts are
one-off data-prep tooling, not part of the app's request path.

## Design decisions & assumptions

- **Vehicle constants are fixed**, not per-request parameters (500-mile
  range, 50-gallon tank, 10 mpg). They live in one place (`constants.py`) if this ever needs to
  become configurable.
- **On-route threshold of 10 miles**: a station is only considered
  reachable/relevant if it's within 10 miles of the route polyline -
  reasonable for a highway detour, avoids suggesting stations that would add
  significant driving time.
- **Partial fills are allowed** (buy exactly what's needed to reach a cheaper
  station, not just full tanks) - this is what makes the greedy
  "next-cheaper-station" strategy provably optimal.
- **Only 1 GraphHopper call per request**
- **Dataset coverage**: geocoding ~4,000+ stations against a rate-limited
  free-tier API takes hours, so this was scoped to a representative subset of
  states (currently 16, ~65% of the full dataset).

## External APIs used

| API | Purpose | Free tier docs |
|---|---|---|
| [GraphHopper Routing API](https://docs.graphhopper.com/) | Turn-by-turn route + polyline between two coordinates | https://www.graphhopper.com/ |
| [Geoapify Geocoding API](https://apidocs.geoapify.com/docs/geocoding/) | Free-text address -> coordinates (used both for user input and for the fuel-station dataset prep) | https://myprojects.geoapify.com/ |
