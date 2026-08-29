"""PagoTotal MX Retail - high-volume e-commerce. See docs/prd.md section 20."""

from worker.schemas.merchant_profile import MerchantProfile

PROFILE = MerchantProfile(
    merchant_id="merchant-a",
    name="PagoTotal MX Retail",
    country="MX",
    currency="MXN",
    payment_methods=["card", "cash"],
    providers=["stripe", "dlocal"],
    archetype="high-volume e-commerce",
)
