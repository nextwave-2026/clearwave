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
worker/helpers/telemetry.py; incident scoping/effects in
worker/helpers/incident.py; merchant profile lookup in
worker/helpers/merchant.py; CLI flags in worker/cli.py.

Run from the clearwave/ repo root as a module, not as a bare script, since
this package uses absolute `worker.*` imports:

    python -m worker.worker merchant-a
    python -m worker.worker merchant-b --incident-provider adyen --interval-seconds 0.5
    python -m worker.worker merchant-c --incident-issuing-bank "Nu Brasil"
    python -m worker.worker merchant-c --scenario provider-issuer-confounded

Healthy-traffic workers loop until SIGINT or SIGTERM. A --scenario run
also stops when --scenario-duration-seconds elapses (default 900). Both
paths run the finally block: pending deliveries flushed, control consumer
closed, C6 record closed. --incident-* flags set the starting incident;
while running, it also polls worker.helpers.control.CONTROL_TOPIC each
tick, so worker/inject.py (or W4's judge trigger) can start or stop an
incident live without restarting this process.

--scenario runs one of the guaranteed scenarios in docs/scenarios.md
instead: same Incident mechanism underneath, but it also records the C6
injected configuration to the quarantined ground-truth store
(worker/ground_truth/) on start and the observation on shutdown. Each
scenario is pinned to one merchant (worker/ground_truth/scenarios.py) -
running it against a different merchant_type is a startup error, not a
silent no-op.
"""

import os
from pathlib import Path

from confluent_kafka import Producer as ConfluentProducer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer
from confluent_kafka.serialization import MessageField, SerializationContext, StringSerializer

from worker.cli import build_incident, build_scenario_run, parse_args
from worker.ground_truth.runner import ScenarioRun
from worker.helpers.control import IncidentControl
from worker.helpers.incident import SPIKE, Incident
from worker.helpers.merchant import Merchant
from worker.helpers.payment import PaymentAttemptBuilder
from worker.helpers.telemetry import TelemetrySampleBuilder
from worker.runtime import RunStopper, WorkerStop

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

    def flush(self, timeout: float = 10.0) -> None:
        remaining = self._producer.flush(timeout)
        if remaining:
            print(f"warning: {remaining} messages still unsent after flush")


def run(
    merchant: Merchant,
    incident: Incident | None,
    interval_seconds: float,
    telemetry_every: int,
    scenario_run: ScenarioRun | None = None,
    duration_seconds: float | None = None,
    producer: Producer | None = None,
    control: IncidentControl | None = None,
    stopper: RunStopper | None = None,
    install_signal_handlers: bool = True,
) -> None:
    producer = producer if producer is not None else Producer()
    control = control if control is not None else IncidentControl(
        merchant.merchant_id, initial=incident
    )
    payment_builder = PaymentAttemptBuilder(merchant, incident=incident)
    telemetry_builder = TelemetrySampleBuilder(merchant, incident=incident)
    stopper = stopper if stopper is not None else RunStopper(duration_seconds)
    previous_handlers = stopper.arm_signals() if install_signal_handlers else None
    tick = 0
    try:
        try:
            while True:
                if stopper.duration_elapsed():
                    print("scenario duration elapsed, stopping...")
                    break
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
                            producer.send(
                                "attempt", key=attempt["payment_id"], event=attempt
                            )
                        extra_closed = payment_builder.build_closed(extra_attempts)
                        producer.send(
                            "closed", key=extra_closed["payment_id"], event=extra_closed
                        )
                        if scenario_run is not None:
                            scenario_run.observe(extra_attempts, extra_closed)

                tick += 1
                if telemetry_every > 0 and tick % telemetry_every == 0:
                    sample = telemetry_builder.build()
                    producer.send("telemetry", key=sample["service_id"], event=sample)

                stopper.sleep(interval_seconds)
        except WorkerStop as stop:
            print(f"stopping on {stop.reason}, flushing pending deliveries...")
        except KeyboardInterrupt:
            print("stopping, flushing pending deliveries...")
    finally:
        try:
            producer.flush(timeout=10.0)
        finally:
            try:
                control.close()
            finally:
                try:
                    if scenario_run is not None:
                        scenario_run.close()
                        print(
                            f"ground truth recorded: instance_id={scenario_run.instance_id}"
                        )
                finally:
                    if previous_handlers is not None:
                        stopper.restore(previous_handlers)


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
        duration_seconds=(
            args.scenario_duration_seconds if scenario_run is not None else None
        ),
    )
