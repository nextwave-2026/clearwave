"""Injection endpoint: publishes one live incident-control command to a
running worker. Per docs/ownership.md, the control surface belongs to W4 and
the injection endpoint behind it is W1's - this is that endpoint. W4's judge
toggle (`surfaces/inject.py`) calls `start_command`/`stop_command`/`publish`
from here rather than hand-rolling the JSON, so the two can never drift.

    python -m worker.inject merchant-b --issuing-bank "Nu Brasil"
    python -m worker.inject merchant-b --provider adyen --effect outage
    python -m worker.inject merchant-b --provider adyen --effect latency --latency-ms 9000
    python -m worker.inject merchant-b --provider adyen --effect spike
    python -m worker.inject merchant-b --stop

The target worker must already be running and consuming
worker.helpers.control.CONTROL_TOPIC - this only publishes the command. See
worker/helpers/incident.py for what each --effect does.

`confluent_kafka` is imported inside `publish` rather than at module scope, so
importing this module to build a command costs no Kafka client. That is what
lets W4's adapter and its offline tests reuse the real command shape without a
broker or the client library present.
"""

import argparse
import json
import os

from worker.helpers.control import CONTROL_TOPIC
from worker.helpers.incident import (
    DECLINE,
    DEFAULT_INCIDENT_DECLINE_REASON,
    DEFAULT_INCIDENT_LATENCY_MS,
    EFFECTS,
)


def start_command(
    merchant_id: str,
    *,
    provider: str | None = None,
    issuing_bank: str | None = None,
    payment_method: str | None = None,
    card_network: str | None = None,
    effect: str = DECLINE,
    decline_reason: str = DEFAULT_INCIDENT_DECLINE_REASON,
    latency_ms: int = DEFAULT_INCIDENT_LATENCY_MS,
) -> dict:
    """The one start command shape every caller publishes.

    Carries a cohort scope and an effect and nothing else - never a scenario
    identifier, which is what keeps the hidden truth hidden from anything
    downstream of the worker.
    """
    scope = {
        dimension: value
        for dimension, value in {
            "provider": provider,
            "issuing_bank": issuing_bank,
            "payment_method": payment_method,
            "card_network": card_network,
        }.items()
        if value
    }
    if not scope:
        raise ValueError(
            "an injected incident needs at least one scoped dimension: "
            "provider, issuing_bank, payment_method or card_network"
        )
    if effect not in EFFECTS:
        raise ValueError(f"unknown effect {effect!r}, expected one of: {EFFECTS}")
    return {
        "merchant_id": merchant_id,
        "action": "start",
        "scope": scope,
        "effect": effect,
        "decline_reason": decline_reason,
        "latency_ms": latency_ms,
    }


def stop_command(merchant_id: str) -> dict:
    """Clear whatever incident that worker is currently running."""
    return {"merchant_id": merchant_id, "action": "stop"}


def publish(command: dict, bootstrap_servers: str | None = None) -> None:
    """Publish one control command, flushing before returning.

    Raises rather than swallowing: a caller that cannot reach the broker has to
    be able to say so instead of reporting an incident that never fired.
    """
    from confluent_kafka import Producer

    servers = bootstrap_servers or os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    producer = Producer({"bootstrap.servers": servers})
    producer.produce(CONTROL_TOPIC, json.dumps(command).encode("utf-8"))
    remaining = producer.flush(timeout=10.0)
    if remaining:
        raise RuntimeError(
            f"{remaining} control command(s) still unsent after flush - "
            f"{servers} did not acknowledge the publish"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("merchant_id", help="Target worker's merchant id, e.g. merchant-b")
    parser.add_argument("--provider")
    parser.add_argument("--issuing-bank")
    parser.add_argument("--payment-method")
    parser.add_argument("--card-network")
    parser.add_argument(
        "--effect",
        choices=EFFECTS,
        default=DECLINE,
        help=f"default: {DECLINE!r}. outage requires --provider only.",
    )
    parser.add_argument(
        "--decline-reason",
        default=DEFAULT_INCIDENT_DECLINE_REASON,
        help=f"used when --effect decline. default: {DEFAULT_INCIDENT_DECLINE_REASON!r}",
    )
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=DEFAULT_INCIDENT_LATENCY_MS,
        help=f"used when --effect latency. default: {DEFAULT_INCIDENT_LATENCY_MS}",
    )
    parser.add_argument("--stop", action="store_true", help="clear the active incident")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.stop:
        command = stop_command(args.merchant_id)
    else:
        try:
            command = start_command(
                args.merchant_id,
                provider=args.provider,
                issuing_bank=args.issuing_bank,
                payment_method=args.payment_method,
                card_network=args.card_network,
                effect=args.effect,
                decline_reason=args.decline_reason,
                latency_ms=args.latency_ms,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    publish(command)
    print(f"published to {CONTROL_TOPIC}: {command}")


if __name__ == "__main__":
    main()
