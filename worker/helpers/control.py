"""Live incident control: lets an external trigger (eventually W4's judge
control) start or stop an Incident on an already-running worker, without
restarting it.

Topic: incidents.control. Plain JSON, not schema-registered - this is
internal to W1's own process, not part of any published C1 contract.
Messages:

    {"merchant_id": "merchant-b", "action": "start",
     "scope": {"issuing_bank": "Nu Brasil"}, "effect": "decline",
     "decline_reason": "do_not_honor"}
    {"merchant_id": "merchant-b", "action": "start",
     "scope": {"provider": "adyen"}, "effect": "decline",
     "decline_probability": 0.35}
    {"merchant_id": "merchant-b", "action": "start",
     "scope": {"provider": "adyen"}, "effect": "outage"}
    {"merchant_id": "merchant-b", "action": "start",
     "scope": {"provider": "adyen"}, "effect": "latency", "latency_ms": 8000}
    {"merchant_id": "merchant-b", "action": "stop"}

"effect" defaults to "decline" when omitted, and "decline_probability" to
the near-total break in worker.helpers.incident, so a command written
before that field existed still means exactly what it meant then. See
worker.helpers.incident for what each effect does and outage's
provider-only restriction.

A message not naming this worker's merchant_id is ignored, so one shared
topic works for every running merchant process. Each worker consumes with
a fresh, uncommitted consumer group so it only reacts to commands sent
after it started - a restarted worker never replays stale commands.

`confluent_kafka` is imported where it is used rather than at module scope, so
that importing CONTROL_TOPIC costs no Kafka client. W4's judge toggle reuses
this topic name and W1's command shape (worker/inject.py), and its tests run
offline.
"""

import json
import os
import uuid

from worker.helpers.incident import (
    DECLINE,
    DEFAULT_INCIDENT_DECLINE_REASON,
    DEFAULT_INCIDENT_LATENCY_MS,
    INCIDENT_DECLINE_PROBABILITY,
    Incident,
)

CONTROL_TOPIC = "incidents.control"


def _ensure_topic_exists(bootstrap_servers: str, topic: str) -> None:
    """Create the topic if it doesn't exist yet, and wait for that to land.

    Matters specifically because of auto.offset.reset=latest below: if the
    topic doesn't exist when the consumer subscribes, "latest" resolves
    whenever the topic is *later* discovered - which can be after a message
    was already published, permanently skipping it rather than just being
    slow to notice it. Creating the topic first removes the race instead of
    trying to out-poll it.
    """
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    futures = admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
    for _, future in futures.items():
        try:
            future.result()
        except Exception as exc:  # noqa: BLE001 - librdkafka raises KafkaException subtypes
            if "already exists" not in str(exc).lower():
                raise


class IncidentControl:
    def __init__(self, merchant_id: str, initial: Incident | None = None, consumer=None):
        """`consumer` is the seam the offline tests drive, the same shape as
        W4's `fire_hidden_incident(publisher=...)`: pass one and no Kafka
        client is imported or constructed, so the command vocabulary can be
        tested without a broker. The default builds the real consumer.
        """
        self.merchant_id = merchant_id
        self.incident = initial
        if consumer is not None:
            self._consumer = consumer
            return

        from confluent_kafka import Consumer

        bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        _ensure_topic_exists(bootstrap_servers, CONTROL_TOPIC)
        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": f"incident-control-{merchant_id}-{uuid.uuid4().hex[:8]}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            }
        )
        self._consumer.subscribe([CONTROL_TOPIC])

    def poll(self) -> None:
        """Non-blocking check for one new control message; updates
        self.incident in place if it applies to this merchant.
        """
        msg = self._consumer.poll(timeout=0)
        if msg is None or msg.error():
            return
        try:
            command = json.loads(msg.value())
        except (json.JSONDecodeError, TypeError):
            print(f"incident control: ignoring invalid JSON: {msg.value()!r}")
            return
        if command.get("merchant_id") != self.merchant_id:
            return

        action = command.get("action")
        if action == "stop":
            print("incident control: cleared active incident")
            self.incident = None
        elif action == "start":
            try:
                self.incident = Incident(
                    scope=command.get("scope", {}),
                    effect=command.get("effect", DECLINE),
                    decline_reason=command.get("decline_reason", DEFAULT_INCIDENT_DECLINE_REASON),
                    decline_probability=command.get(
                        "decline_probability", INCIDENT_DECLINE_PROBABILITY
                    ),
                    latency_ms=command.get("latency_ms", DEFAULT_INCIDENT_LATENCY_MS),
                )
            except (ValueError, TypeError) as exc:
                print(f"incident control: rejected start command: {exc}")
                return
            # The probability is printed for decline only, and appended rather
            # than woven in, so the line documented in docs/pitch.md still reads
            # as its own prefix. The operator running the mild-then-hard demo
            # needs to see which of the two landed.
            magnitude = (
                f" at decline_probability={self.incident.decline_probability}"
                if self.incident.effect == DECLINE
                else ""
            )
            print(
                f"incident control: now targeting {self.incident.scope} "
                f"with effect={self.incident.effect}{magnitude}"
            )
        else:
            print(f"incident control: ignoring unknown action {action!r}")

    def close(self) -> None:
        self._consumer.close()
