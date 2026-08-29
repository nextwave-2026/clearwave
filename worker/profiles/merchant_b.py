"""PagoTotal CO Subscriptions - subscription / recurring. See docs/prd.md section 20."""

from worker.schemas.merchant_profile import MerchantProfile

PROFILE = MerchantProfile(
    merchant_id="merchant-b",
    name="PagoTotal CO Subscriptions",
    country="CO",
    currency="COP",
    payment_methods=["card", "pse"],
    providers=["adyen", "mercadopago"],
    archetype="subscription / recurring",
)
