# Two simultaneous incidents

This is the measured worked example for the scored result "two simultaneous incidents correctly separated and prioritized". Traffic, incidents and outages in this example are simulated demo data produced by Clearwave and do not represent or imply a real incident or service problem at any named company.

## Scenario and commands

The isolated stack used project `clearwave-two-incidents`, Kafka host port `29092`, schema registry host port `28081`, dashboard host port `28082`, and store `state/two-incidents/clearwave.db`. The temporary compose override used the same remapped mounts as `scripts/verify-demo.compose.yml` and was not part of the product.

Exact preparation and injection commands:

```sh
mkdir -p state/two-incidents/ground_truth/{merchant-a,merchant-b,merchant-c}
./.venv/bin/python -S scripts/prepare_history.py --db state/two-incidents/clearwave.db
docker compose -p clearwave-two-incidents -f docker-compose.yml -f scripts/verify-demo.compose.yml -f scripts/.two-incidents.compose.yml up -d --wait --wait-timeout 420 kafka schema-registry worker-merchant-a worker-merchant-b worker-merchant-c detector investigation surfaces

docker compose -p clearwave-two-incidents -f docker-compose.yml -f scripts/verify-demo.compose.yml -f scripts/.two-incidents.compose.yml exec -T worker-merchant-b python -m worker.inject merchant-b --provider adyen --decline-reason provider_timeout --decline-probability 0.95
docker compose -p clearwave-two-incidents -f docker-compose.yml -f scripts/verify-demo.compose.yml -f scripts/.two-incidents.compose.yml exec -T worker-merchant-c python -m worker.inject merchant-c --issuing-bank 'Nu Brasil' --decline-reason do_not_honor --decline-probability 0.95
```

The two commands were issued one second apart after the workers were consuming the control topic:

- merchant-b, Colombia: provider `adyen`, `provider_timeout`, 95% decline probability.
- merchant-c, Brazil: issuing bank `Nu Brasil`, `do_not_honor`, 95% decline probability.

The first attempted host-side publish was discarded by the broker's remapped advertised listener and was not counted. The two `docker compose exec` commands above were the delivered controls; worker logs confirmed both target scopes.

## Measured result before this branch

At 2026-08-30T11:50Z the store had records for both faults. At 11:57:05Z the API returned three active high records:

| record | cohort | approval | loss per hour | GMV at risk |
| --- | --- | ---: | ---: | ---: |
| `inc-2026-08-30-204a67eb` | provider `adyen` | 0.780269 vs 0.866150 | USD 14,288.08 | USD 1,190.67 |
| `inc-2026-08-30-81d1be0a` | merchant-c, BR, issuing bank `Nu Brasil` | 0.329897 vs 0.872137 | USD 38,964.31 | USD 3,247.03 |
| `inc-2026-08-30-fa05ebed` | BR, `Nu Brasil`, provider `mercadopago` | 0.266667 vs 0.910969 | USD 20,437.72 | USD 1,703.14 |

This is **not fully correct separation** on the then-current main: the two independent faults were distinguishable, but the bank fault was over-split into its all-provider row and a provider child row. The detector lifecycle fix in PR #88 owns that boundary and must land before claiming that the live detector produces exactly two records. No investigation conclusion crossed the fault boundary: the Adyen result cited Adyen/provider observations, while the Nu Brasil result cited the BR merchant and bank slice, all four observed providers, and sibling banks. Neither result named the other's incident or borrowed its traffic.

The board did not hide either story. `/api/overview` exposed all three active rows and `/api/escalations` exposed a separate group for each. The extra child row made the presentation misleading by presenting one fault twice.

Before this branch, all three rows were `high`. `surfaces.store._priority_key` then used only `(severity, incident_id)`. The observed board order put Adyen first even though the Nu Brasil merchant-c row had the largest measured loss per hour. The tie-break was therefore incidental, not defensible.

Both diagnosed high incidents escalated independently to dashboard and Slack. The escalation store contained distinct channel outcomes for each incident. No phone call was expected because the binding is dashboard plus Slack for `high`; phone is reserved for `critical`. A watch row did not escalate.

## Rule after this branch

The queue orders by stored severity, then descending measured `loss_per_hour`, then descending `gmv_at_risk`, then incident ID for a deterministic final tie-break. In one sentence: **the most severe incident is first, and equal-severity incidents are ordered by the business loss they are costing per hour.** The page states this rule and still renders every queue row.

The same commands were rerun after this branch's ordering change against a fresh prepared store. At 12:08:28Z the API order was `critical` merchant-c/Nu Brasil (USD 53,029.12/hour), then `high` merchant-b/Adyen (USD 24,030.46/hour), then `high` merchant-c/Nu Brasil/payment-method pix (USD 15,788.44/hour). This is the expected severity-first and loss-per-hour ordering. The rerun still showed three active rows because PR #88 had not landed: the bank fault's payment-method slice was still a duplicate story. This after-run validates the surface ordering only; it does not claim the detector separation is fixed.

The regression test in `tests/test_surfaces.py` pins severity-first ordering and both business-impact tie-breaks. The detector separation result remains owned by PR #88 rather than being duplicated or changed here.

Clearwave diagnoses and advises only. It does not reroute, remediate, or otherwise act on either incident.
