"""Loads the geocoded fuel-station dataset used by the optimizer.

Station data lives in the DB (see fueloptimizer.models.FuelStation), not in
CSV files - the CSVs in the repo root are only used by the one-off/local
scripts that populate this table (resolve_address.py geocodes addresses
into the CSV; a separate, non-committed loader script syncs the CSV into
this table). Everything the app itself does at request time reads from
the DB.
"""

from dataclasses import dataclass

from fueloptimizer.models import FuelStation as FuelStationModel


@dataclass(frozen=True)
class FuelStation:
    station_id: str
    name: str
    city: str
    state: str
    price_per_gallon: float
    lat: float
    lng: float


_stations_cache: list[FuelStation] | None = None


def load_stations(force_reload: bool = False) -> list[FuelStation]:
    """
    Load fuel stations from the DB.

    Cached in-process after the first call; pass force_reload=True (mainly
    for tests, or after the DB table has been re-synced from the CSV) to
    bypass the cache and re-query the DB.
    """
    global _stations_cache

    if _stations_cache is not None and not force_reload:
        return _stations_cache

    stations = [
        FuelStation(
            station_id=row.station_id,
            name=row.name,
            city=row.city,
            state=row.state,
            price_per_gallon=row.price_per_gallon,
            lat=row.lat,
            lng=row.lng,
        )
        for row in FuelStationModel.objects.all()
    ]

    _stations_cache = stations
    return stations


def invalidate_cache() -> None:
    """
    Drop the in-process station cache so the next load_stations() call
    re-queries the DB. Call this after writing new/updated rows to the
    FuelStation table (e.g. from the register-station endpoint) so the
    change is picked up immediately, without needing a server restart.
    """
    global _stations_cache
    _stations_cache = None
