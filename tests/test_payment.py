"""Focused test for per-currency amount ranges in PaymentAttemptBuilder.

The single currency-blind draw made COP tickets price as pocket change
and capped their severity. Ranges are chosen so all three merchants land
in ~USD 8-120 after FX conversion.
"""

from __future__ import annotations

from types import SimpleNamespace

import unittest
from unittest import mock

from worker.helpers.merchant import Merchant
from worker.helpers.payment import PaymentAttemptBuilder, CURRENCY_RANGES


class PaymentCurrencyRangeTests(unittest.TestCase):
    def test_mxn_uses_its_configured_range(self):
        merchant = Merchant("merchant-a")
        builder = PaymentAttemptBuilder(merchant)
        with mock.patch("worker.helpers.payment.random.randint") as mock_randint:
            mock_randint.side_effect = [99999] + [150] * 30
            attempts = builder.build_chain()
            # first randint call inside build_chain is always the amount draw
            self.assertEqual(mock_randint.call_args_list[0].args, (15_000, 220_000))
            self.assertEqual(attempts[0]["amount_minor"], 99999)

    def test_cop_uses_its_configured_range(self):
        merchant = Merchant("merchant-b")
        builder = PaymentAttemptBuilder(merchant)
        with mock.patch("worker.helpers.payment.random.randint") as mock_randint:
            mock_randint.side_effect = [12345678] + [150] * 30
            attempts = builder.build_chain()
            self.assertEqual(mock_randint.call_args_list[0].args, (3_200_000, 48_000_000))
            self.assertEqual(attempts[0]["amount_minor"], 12345678)

    def test_brl_uses_its_configured_range(self):
        merchant = Merchant("merchant-c")
        builder = PaymentAttemptBuilder(merchant)
        with mock.patch("worker.helpers.payment.random.randint") as mock_randint:
            mock_randint.side_effect = [12345] + [150] * 30
            attempts = builder.build_chain()
            self.assertEqual(mock_randint.call_args_list[0].args, (4_300, 65_000))
            self.assertEqual(attempts[0]["amount_minor"], 12345)

    def test_unknown_currency_falls_back_to_original_range(self):
        # Builder only reads .currency (and other attrs later); a fake lets us
        # hit the explicit fallback without altering merchant registry.
        fake_merchant = SimpleNamespace(
            currency="XYZ",
            country="MX",
            payment_methods=["card"],
            providers=["p1"],
            merchant_id="fake-m",
            merchant_type="fake",
            name="Fake",
            archetype="test",
        )
        builder = PaymentAttemptBuilder(fake_merchant)
        with mock.patch("worker.helpers.payment.random.randint") as mock_randint:
            mock_randint.side_effect = [12345] + [150] * 30
            attempts = builder.build_chain()
            self.assertEqual(mock_randint.call_args_list[0].args, (1000, 50000))
            self.assertEqual(attempts[0]["amount_minor"], 12345)


if __name__ == "__main__":
    unittest.main()
