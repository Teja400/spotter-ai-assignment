from django.db import models


class FuelStation(models.Model):
    """
    A geocoded fuel station, sourced from the cleaned/resolved fuel-price
    dataset (see resolve_address.py). This is the single source of truth
    used by the optimizer at request time - all station data lives here in
    the DB, not in CSV files.
    """

    station_id = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=10, blank=True)
    price_per_gallon = models.FloatField()
    lat = models.FloatField()
    lng = models.FloatField()

    class Meta:
        indexes = [
            models.Index(fields=["lat", "lng"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.station_id})"
