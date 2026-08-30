"""Command-line surface for worker/worker.py: argument parsing and turning
those arguments into an Incident or a ScenarioRun. Split out of worker.py
because the flag set (manual --incident-* plus --scenario) got long enough
to bury the actual producer/loop logic next to it.
"""

import argparse
import os

from worker.ground_truth.runner import ScenarioRun
from worker.ground_truth.scenarios import SCENARIOS
from worker.helpers.incident import (
    DECLINE,
    DEFAULT_INCIDENT_DECLINE_REASON,
    DEFAULT_INCIDENT_LATENCY_MS,
    EFFECTS,
    Incident,
)
from worker.helpers.merchant import PROFILES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously simulate and publish payment-attempt, "
        "payment-closed, and ops-telemetry events for a merchant."
    )
    parser.add_argument(
        "merchant_type",
        nargs="?",
        default=os.environ.get("MERCHANT_TYPE", "merchant-a"),
        help="Merchant profile to simulate (default: env MERCHANT_TYPE or "
        "merchant-a). One of: " + ", ".join(sorted(PROFILES)),
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default=os.environ.get("SCENARIO"),
        help="Run a named guaranteed scenario (docs/scenarios.md) instead of "
        "a manual --incident-*: builds the right Incident, records the C6 "
        "injected configuration in the quarantined ground-truth store on "
        "start, and the observation on shutdown. Requires running the "
        "scenario's own merchant - mismatched merchant_type is an error. "
        "Mutually exclusive with --incident-*.",
    )
    parser.add_argument(
        "--scenario-duration-seconds",
        type=int,
        default=int(os.environ.get("SCENARIO_DURATION_SECONDS", "900")),
        help="How long a --scenario run lasts, in seconds. Recorded on the "
        "C6 record and enforced as the process lifetime (default: env "
        "SCENARIO_DURATION_SECONDS or 900). Ignored when --scenario is not "
        "set, so healthy-traffic workers stay unbounded.",
    )
    parser.add_argument(
        "--incident-provider",
        default=os.environ.get("INCIDENT_PROVIDER"),
        help="Scope an incident to this provider (e.g. stripe). Combine "
        "with the other --incident-* flags to narrow further; omit all of "
        "them for fully normal traffic.",
    )
    parser.add_argument(
        "--incident-issuing-bank",
        default=os.environ.get("INCIDENT_ISSUING_BANK"),
        help="Scope an incident to this issuing bank (e.g. 'Banco Real').",
    )
    parser.add_argument(
        "--incident-payment-method",
        default=os.environ.get("INCIDENT_PAYMENT_METHOD"),
        help="Scope an incident to this payment method (e.g. card).",
    )
    parser.add_argument(
        "--incident-card-network",
        default=os.environ.get("INCIDENT_CARD_NETWORK"),
        help="Scope an incident to this card network (e.g. mastercard).",
    )
    parser.add_argument(
        "--incident-effect",
        choices=EFFECTS,
        default=os.environ.get("INCIDENT_EFFECT", DECLINE),
        help="What the incident does to matching traffic: decline (elevated "
        "decline rate), outage (provider excluded from routing, volume to "
        "zero - provider scope only), latency (approves/declines normally "
        "but latency/queue spike), or spike (extra volume, no per-attempt "
        f"change). Default: env INCIDENT_EFFECT or {DECLINE!r}.",
    )
    parser.add_argument(
        "--incident-decline-reason",
        default=os.environ.get("INCIDENT_DECLINE_REASON", DEFAULT_INCIDENT_DECLINE_REASON),
        help="decline_reason attached to incident-affected attempts when "
        "effect=decline (default: env INCIDENT_DECLINE_REASON or "
        f"{DEFAULT_INCIDENT_DECLINE_REASON!r}).",
    )
    parser.add_argument(
        "--incident-latency-ms",
        type=int,
        default=int(os.environ.get("INCIDENT_LATENCY_MS", str(DEFAULT_INCIDENT_LATENCY_MS))),
        help="latency_ms attached to incident-affected attempts when "
        f"effect=latency (default: env INCIDENT_LATENCY_MS or {DEFAULT_INCIDENT_LATENCY_MS}).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.environ.get("EVENT_INTERVAL_SECONDS", "1.0")),
        help="Delay between payment attempts (default: env "
        "EVENT_INTERVAL_SECONDS or 1.0).",
    )
    parser.add_argument(
        "--telemetry-every",
        type=int,
        default=int(os.environ.get("TELEMETRY_EVERY", "5")),
        help="Emit one ops.telemetry sample every N attempts (default: env "
        "TELEMETRY_EVERY or 5). 0 disables telemetry.",
    )
    return parser.parse_args()


def build_incident(args: argparse.Namespace) -> Incident | None:
    scope = {
        "provider": args.incident_provider,
        "issuing_bank": args.incident_issuing_bank,
        "payment_method": args.incident_payment_method,
        "card_network": args.incident_card_network,
    }
    scope = {dimension: value for dimension, value in scope.items() if value}
    if not scope:
        return None
    return Incident(
        scope=scope,
        effect=args.incident_effect,
        decline_reason=args.incident_decline_reason,
        latency_ms=args.incident_latency_ms,
    )


def build_scenario_run(args: argparse.Namespace) -> ScenarioRun | None:
    if not args.scenario:
        return None
    manual_flags = (
        args.incident_provider,
        args.incident_issuing_bank,
        args.incident_payment_method,
        args.incident_card_network,
    )
    if any(manual_flags):
        raise SystemExit("--scenario and --incident-* are mutually exclusive")
    definition = SCENARIOS[args.scenario]
    if args.merchant_type != definition.merchant_id:
        raise SystemExit(
            f"scenario {args.scenario!r} runs on {definition.merchant_id!r}, "
            f"not {args.merchant_type!r} - run: "
            f"python -m worker.worker {definition.merchant_id} --scenario {args.scenario}"
        )
    return ScenarioRun(args.scenario, duration_seconds=args.scenario_duration_seconds)
