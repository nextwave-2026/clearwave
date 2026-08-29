"""Runtime lookup for merchant profiles.

Assembles the individual profiles from worker.profiles (domain shape defined
in worker.schemas.merchant_profile) into the registry Merchant() resolves
against. One process = one merchant, selected by merchant_type.
"""

from worker.profiles import merchant_a, merchant_b, merchant_c

PROFILES = {
    merchant_a.PROFILE.merchant_id: merchant_a.PROFILE,
    merchant_b.PROFILE.merchant_id: merchant_b.PROFILE,
    merchant_c.PROFILE.merchant_id: merchant_c.PROFILE,
}


class Merchant:
    """One simulated merchant instance, selected by type.

    Per-event variation (amount, latency, decline, issuing bank, ...) is
    layered on top by the event builder in worker.py; this class only fixes
    what a given merchant is.
    """

    def __init__(self, merchant_type: str):
        try:
            profile = PROFILES[merchant_type]
        except KeyError:
            valid = ", ".join(sorted(PROFILES))
            raise ValueError(
                f"unknown merchant type {merchant_type!r}, expected one of: {valid}"
            ) from None

        self.merchant_type = merchant_type
        self.merchant_id = profile.merchant_id
        self.name = profile.name
        self.country = profile.country
        self.currency = profile.currency
        self.payment_methods = profile.payment_methods
        self.providers = profile.providers
        self.archetype = profile.archetype
