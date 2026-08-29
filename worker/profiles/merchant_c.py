"""Mercado Livre (BR) - LATAM marketplace, diverse methods. See docs/prd.md section 20."""

from worker.schemas.merchant_profile import MerchantProfile

PROFILE = MerchantProfile(
    merchant_id="merchant-c",
    name="Mercado Livre",
    country="BR",
    currency="BRL",
    payment_methods=["card", "pix"],
    providers=["stripe", "adyen", "dlocal", "mercadopago"],
    archetype="LATAM marketplace, diverse methods",
)
