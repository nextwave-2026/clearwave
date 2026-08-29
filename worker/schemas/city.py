"""Domain schema for a city used as a transaction's geographic dimension.

Shape only - the actual per-country city data lives in worker/reference/geography.py,
same split as MerchantProfile (schemas/) vs the merchant instances (profiles/).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    name: str
    country: str
    lat: float
    lon: float
