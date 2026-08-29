#!/bin/sh
# Read and pretty-print messages from one of W1's Kafka topics, decoding the
# Confluent JSON Schema wire format (magic byte + schema id) via Schema
# Registry - a plain kafka-console-consumer only shows the raw bytes.
#
# Usage (run from the repo root, wherever KAFKA_BOOTSTRAP_SERVERS and
# SCHEMA_REGISTRY_URL are reachable - e.g. inside the devspace container):
#
#   ./worker/read_kafka.sh payments.attempts
#   ./worker/read_kafka.sh payments.closed 5
#   AUTO_OFFSET_RESET=latest ./worker/read_kafka.sh ops.telemetry
#
# Defaults to the 10 earliest messages still retained on the topic.

set -eu

TOPIC="${1:?usage: read_kafka.sh <payments.attempts|payments.closed|ops.telemetry> [count]}"
COUNT="${2:-10}"

case "$TOPIC" in
  payments.attempts) SCHEMA_FILE="worker/registry/payment_attempt.schema.json" ;;
  payments.closed)   SCHEMA_FILE="worker/registry/payment_closed.schema.json" ;;
  ops.telemetry)      SCHEMA_FILE="worker/registry/ops_telemetry.schema.json" ;;
  *)
    echo "unknown topic '$TOPIC', expected one of: payments.attempts, payments.closed, ops.telemetry" >&2
    exit 1
    ;;
esac

python3 - "$TOPIC" "$COUNT" "$SCHEMA_FILE" <<'PY'
import json
import os
import sys

from confluent_kafka import Consumer
from confluent_kafka.schema_registry.json_schema import JSONDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext

topic, count, schema_file = sys.argv[1], int(sys.argv[2]), sys.argv[3]

# JSONDeserializer validates against this local schema string directly and
# only strips the wire-format header - it doesn't need a registry client to
# do that (unlike the serializer, which needs one to register/resolve).
deserializer = JSONDeserializer(open(schema_file).read())

consumer = Consumer(
    {
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        "group.id": f"read-kafka-cli-{os.getpid()}",
        "auto.offset.reset": os.environ.get("AUTO_OFFSET_RESET", "earliest"),
    }
)
consumer.subscribe([topic])

seen = 0
try:
    while seen < count:
        msg = consumer.poll(5)
        if msg is None:
            print(f"(no more messages after {seen})", file=sys.stderr)
            break
        if msg.error():
            print(f"error: {msg.error()}", file=sys.stderr)
            continue
        value = deserializer(msg.value(), SerializationContext(topic, MessageField.VALUE))
        key = msg.key().decode("utf-8") if msg.key() else None
        print(
            json.dumps(
                {"key": key, "partition": msg.partition(), "offset": msg.offset(), "value": value},
                indent=2,
                default=str,
            )
        )
        seen += 1
finally:
    consumer.close()
PY
