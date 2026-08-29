"""W1 C1 producer: continuously builds simulated payment-attempt events for a
merchant and publishes them to Kafka, JSON-Schema-encoded against the
schemas registered in Schema Registry. Three topics, matching what W2
(andres) asked for in README-FOR-RAUL.md:

    payments.attempts  - one per provider attempt, keyed by payment_id.
                           A payment may produce several (retry chain).
    payments.closed     - one per payment once its chain stops (approved,
                           exhausted, or abandoned), keyed by payment_id -
                           see PaymentAttemptBuilder.build_chain/build_closed
    ops.telemetry        - periodic per-service gauge sample, keyed by
                           service_id, independent of any payment

One process = one merchant, selected via a CLI arg (falls back to the
MERCHANT_TYPE env var) - that is what lets an eventual container image take
the merchant as its CMD arg, one running container per merchant. Event
construction lives in worker/helpers/payment.py and
worker/helpers/telemetry.py; merchant profile lookup in
worker/helpers/merchant.py.

Run from the clearwave/ repo root as a module, not as a bare script, since
this package uses absolute `worker.*` imports:

    python -m worker.worker merchant-a
    python -m worker.worker merchant-a --mode anomaly --interval-seconds 0.5

Loops forever until interrupted (Ctrl+C). No on-the-fly trigger yet - mode is
fixed for the life of the process. Live triggering (a control topic W4 can
publish to) is the next W1 increment and must extend this without breaking
the C1 contract.
"""

import argparse
import os
import time
from pathlib import Path

from confluent_kafka import Producer as ConfluentProducer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer
from confluent_kafka.serialization import MessageField, SerializationContext, StringSerializer

from worker.helpers.merchant import PROFILES, Merchant
from worker.helpers.payment import MODES, NORMAL, PaymentAttemptBuilder
from worker.helpers.telemetry import TelemetrySampleBuilder

REGISTRY_DIR = Path(__file__).parent / "registry"

TOPICS = {
    "attempt": "payments.attempts",
    "closed": "payments.closed",
    "telemetry": "ops.telemetry",
}

SCHEMA_PATHS = {
    "attempt": REGISTRY_DIR / "payment_attempt.schema.json",
    "closed": REGISTRY_DIR / "payment_closed.schema.json",
    "telemetry": REGISTRY_DIR / "ops_telemetry.schema.json",
}


class Producer:
    """One Kafka connection serving all three C1 topics, each with its own
    registered JSON Schema serializer.
    """

    def __init__(self):
        schema_registry_client = SchemaRegistryClient(
            {"url": os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")}
        )
        self._key_serializer = StringSerializer("utf_8")
        self._value_serializers = {
            kind: JSONSerializer(path.read_text(), schema_registry_client)
            for kind, path in SCHEMA_PATHS.items()
        }
        self._producer = ConfluentProducer(
            {
                "bootstrap.servers": os.environ.get(
                    "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
                )
            }
        )

    @staticmethod
    def _on_delivery(err, msg):
        if err is not None:
            print(f"delivery failed: {err}")
            return
        print(
            f"delivered key={msg.key()} to {msg.topic()} "
            f"[partition {msg.partition()}] offset {msg.offset()}"
        )

    def send(self, kind: str, key: str, event: dict) -> None:
        topic = TOPICS[kind]
        value_bytes = self._value_serializers[kind](
            event, SerializationContext(topic, MessageField.VALUE)
        )
        key_bytes = self._key_serializer(
            key, SerializationContext(topic, MessageField.KEY)
        )
        self._producer.produce(
            topic=topic,
            key=key_bytes,
            value=value_bytes,
            on_delivery=self._on_delivery,
        )
        # serves delivery-report callbacks without blocking for one
        self._producer.poll(0)

    def flush(self) -> None:
        self._producer.flush()


def run(
    merchant: Merchant,
    mode: str,
    interval_seconds: float,
    telemetry_every: int,
) -> None:
    producer = Producer()
    payment_builder = PaymentAttemptBuilder(merchant, mode=mode)
    telemetry_builder = TelemetrySampleBuilder(merchant, mode=mode)
    tick = 0
    try:
        while True:
            attempts = payment_builder.build_chain()
            for attempt in attempts:
                producer.send("attempt", key=attempt["payment_id"], event=attempt)

            closed = payment_builder.build_closed(attempts)
            producer.send("closed", key=closed["payment_id"], event=closed)

            tick += 1
            if telemetry_every > 0 and tick % telemetry_every == 0:
                sample = telemetry_builder.build()
                producer.send("telemetry", key=sample["service_id"], event=sample)

            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("stopping, flushing pending deliveries...")
    finally:
        producer.flush()


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
        "--mode",
        choices=MODES,
        default=os.environ.get("EVENT_MODE", NORMAL),
        help="Whether to generate normal events or ones that should be "
        "flagged for review (default: env EVENT_MODE or normal).",
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


if __name__ == "__main__":
    args = parse_args()
    merchant = Merchant(args.merchant_type)
    run(merchant, args.mode, args.interval_seconds, args.telemetry_every)
