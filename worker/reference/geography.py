"""City reference data, keyed by country, for the countries in the PagoTotal
case (docs/challenge.md). Used so a generated event's city/lat/lon stays
consistent with its country instead of being an independent random pair.
"""

import random

from worker.schemas.city import City

CITIES = {
    "MX": [
        City("Ciudad de Mexico", "MX", 19.4326, -99.1332),
        City("Guadalajara", "MX", 20.6597, -103.3496),
        City("Monterrey", "MX", 25.6866, -100.3161),
    ],
    "CO": [
        City("Bogota", "CO", 4.7110, -74.0721),
        City("Medellin", "CO", 6.2442, -75.5812),
        City("Cali", "CO", 3.4516, -76.5320),
    ],
    "BR": [
        City("Sao Paulo", "BR", -23.5505, -46.6333),
        City("Rio de Janeiro", "BR", -22.9068, -43.1729),
        City("Belo Horizonte", "BR", -19.9167, -43.9345),
    ],
}


def pick_city(country: str) -> City:
    try:
        return random.choice(CITIES[country])
    except KeyError:
        valid = ", ".join(sorted(CITIES))
        raise ValueError(f"no cities for country {country!r}, expected one of: {valid}") from None
