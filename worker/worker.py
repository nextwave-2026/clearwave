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
    python -m worker.worker merchant-b --incident-provider adyen --interval-seconds 0.5
    python -m worker.worker merchant-c --incident-issuing-bank "Nu Brasil"
    python -m worker.worker merchant-c --scenario provider-issuer-confounded

Loops forever until interrupted (Ctrl+C). --incident-* flags set the
starting incident; while running, it also polls
worker.helpers.control.CONTROL_TOPIC each tick, so worker/inject.py (or
eventually W4's judge trigger) can start or stop an incident live without
restarting this process.

--scenario runs one of the guaranteed scenarios in docs/scenarios.md
instead: same Incident mechanism underneath, but it also records the C6
injected configuration to the quarantined ground-truth store
(worker/ground_truth/) on start and the observation on shutdown. Each
scenario is pinned to one merchant (worker/ground_truth/scenarios.py) -
running it against a different merchant_type is a startup error, not a
silent no-op.
"""

import argparse
import os
import time
from pathlib import Path

from confluent_kafka import Producer as ConfluentProducer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer
from confluent_kafka.serialization import MessageField, SerializationContext, StringSerializer

from worker.ground_truth.runner import ScenarioRun
from worker.ground_truth.scenarios import SCENARIOS
from worker.helpers.control import IncidentControl
from worker.helpers.merchant import PROFILES, Merchant
from worker.helpers.payment import (
    DECLINE,
    DEFAULT_INCIDENT_DECLINE_REASON,
    DEFAULT_INCIDENT_LATENCY_MS,
    EFFECTS,
    SPIKE,
    Incident,
    PaymentAttemptBuilder,
)
from worker.helpers.telemetry import TelemetrySampleBuilder

SPIKE_MULTIPLIER = 3  # extra chains generated per tick while a spike incident is active

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
    incident: Incident | None,
    interval_seconds: float,
    telemetry_every: int,
    scenario_run: ScenarioRun | None = None,
) -> None:
    producer = Producer()
    control = IncidentControl(merchant.merchant_id, initial=incident)
    payment_builder = PaymentAttemptBuilder(merchant, incident=incident)
    telemetry_builder = TelemetrySampleBuilder(merchant, incident=incident)
    tick = 0
    try:
        while True:
            control.poll()
            payment_builder.incident = control.incident
            telemetry_builder.incident = control.incident

            attempts = payment_builder.build_chain()
            for attempt in attempts:
                producer.send("attempt", key=attempt["payment_id"], event=attempt)

            closed = payment_builder.build_closed(attempts)
            producer.send("closed", key=closed["payment_id"], event=closed)
            if scenario_run is not None:
                scenario_run.observe(attempts, closed)

            active = control.incident
            if active is not None and active.effect == SPIKE:
                # no per-attempt effect - extra volume forced into the
                # scoped cohort is the effect itself
                for _ in range(SPIKE_MULTIPLIER):
                    extra_attempts = payment_builder.build_chain(forced=active.scope)
                    for attempt in extra_attempts:
                        producer.send("attempt", key=attempt["payment_id"], event=attempt)
                    extra_closed = payment_builder.build_closed(extra_attempts)
                    producer.send("closed", key=extra_closed["payment_id"], event=extra_closed)
                    if scenario_run is not None:
                        scenario_run.observe(extra_attempts, extra_closed)

            tick += 1
            if telemetry_every > 0 and tick % telemetry_every == 0:
                sample = telemetry_builder.build()
                producer.send("telemetry", key=sample["service_id"], event=sample)

            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("stopping, flushing pending deliveries...")
    finally:
        producer.flush()
        control.close()
        if scenario_run is not None:
            scenario_run.close()
            print(f"ground truth recorded: instance_id={scenario_run.instance_id}")


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
        help="C6 injection window length (default: env "
        "SCENARIO_DURATION_SECONDS or 900 - the catalogue's typical window).",
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


if __name__ == "__main__":
    args = parse_args()
    merchant = Merchant(args.merchant_type)
    scenario_run = build_scenario_run(args)
    incident = scenario_run.incident if scenario_run else build_incident(args)
    run(
        merchant,
        incident,
        args.interval_seconds,
        args.telemetry_every,
        scenario_run=scenario_run,
    )
