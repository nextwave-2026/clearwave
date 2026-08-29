"""Domain schema for a merchant profile.

This is the shape of a merchant in W1's own model, not a Kafka/Schema
Registry schema - those live under worker/registry/ instead. Individual
merchant instances live under worker/profiles/, one file each.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MerchantProfile:
    merchant_id: str
    name: str
    country: str
    currency: str
    payment_methods: list
    providers: list
    archetype: str
