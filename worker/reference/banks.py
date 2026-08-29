"""Issuing bank reference data, keyed by country.

A small, stable, named pool per country - not Faker - because an incident
needs something repeatable to target ("Provider P2 declines Nu Mexico
cards"). A freshly-invented bank name per attempt can't be the subject of a
scoped incident. Real, recognizable regional banks, not placeholders.

These names are simulated demo identities. Traffic, incidents and outages
generated against them do not represent or imply a real incident or service
problem at any named bank. Real names are used only to make the demonstration
recognisable and realistic.
"""

import random

BANKS = {
    "MX": ["Nu Mexico", "BBVA Mexico", "Banorte"],
    "CO": ["Davivienda", "Bancolombia", "Banco de Bogota"],
    "BR": ["Nu Brasil", "Banco do Brasil", "Itau"],
}


def pick_bank(country: str) -> str:
    try:
        return random.choice(BANKS[country])
    except KeyError:
        valid = ", ".join(sorted(BANKS))
        raise ValueError(f"no banks for country {country!r}, expected one of: {valid}") from None
