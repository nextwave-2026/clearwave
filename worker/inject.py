"""Injection endpoint stand-in: publishes one live incident-control command
to a running worker. Per docs/ownership.md, the control surface belongs to
W4 and the injection endpoint behind it is W1's - this is that endpoint,
callable directly for now until W4 wires a real trigger to it.

    python -m worker.inject merchant-b --issuing-bank "Nu Brasil"
    python -m worker.inject merchant-b --provider adyen --effect outage
    python -m worker.inject merchant-b --provider adyen --effect latency --latency-ms 9000
    python -m worker.inject merchant-b --provider adyen --effect spike
    python -m worker.inject merchant-b --stop

The target worker must already be running and consuming
worker.helpers.control.CONTROL_TOPIC - this only publishes the command. See
worker/helpers/payment.py for what each --effect does.
"""

import argparse
import json
import os

from confluent_kafka import Producer

from worker.helpers.control import CONTROL_TOPIC
from worker.helpers.payment import (
    DECLINE,
    DEFAULT_INCIDENT_DECLINE_REASON,
    DEFAULT_INCIDENT_LATENCY_MS,
    EFFECTS,
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
        command = {"merchant_id": args.merchant_id, "action": "stop"}
    else:
        scope = {
            dimension: value
            for dimension, value in {
                "provider": args.provider,
                "issuing_bank": args.issuing_bank,
                "payment_method": args.payment_method,
                "card_network": args.card_network,
            }.items()
            if value
        }
        if not scope:
            raise SystemExit(
                "specify at least one of --provider/--issuing-bank/"
                "--payment-method/--card-network, or --stop"
            )
        command = {
            "merchant_id": args.merchant_id,
            "action": "start",
            "scope": scope,
            "effect": args.effect,
            "decline_reason": args.decline_reason,
            "latency_ms": args.latency_ms,
        }

    producer = Producer(
        {"bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")}
    )
    producer.produce(CONTROL_TOPIC, json.dumps(command).encode("utf-8"))
    producer.flush()
    print(f"published to {CONTROL_TOPIC}: {command}")


if __name__ == "__main__":
    main()
