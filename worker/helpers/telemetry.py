"""Builds simulated C1 operational telemetry samples (clearwave.ops.v1,
topic ops.telemetry). Periodic per-service gauges, no payment identity -
independent of PaymentAttemptBuilder, keyed by service_id instead of
payment_id. Shape matches worker/registry/ops_telemetry.schema.json.
"""

import random
import uuid
from datetime import datetime, timezone

from worker.helpers.incident import Incident
from worker.helpers.merchant import Merchant


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class TelemetrySampleBuilder:
    def __init__(self, merchant: Merchant, incident: Incident | None = None):
        self.merchant = merchant
        self.incident = incident
        self.service_id = f"w1-worker-{merchant.merchant_type}"
        self.deployment_id = "worker-local"

    def build(self) -> dict:
        now = _now_iso()
        degraded = self.incident is not None
        return {
            "schema": "clearwave.ops.v1",
            "event_id": f"evt_{uuid.uuid4().hex}",
            "emitted_at": now,
            "sample_ts": now,

            "service_id": self.service_id,
            "deployment_id": self.deployment_id,

            "healthy": not degraded,
            "queue_depth": random.randint(500, 2000) if degraded else random.randint(0, 50),
            "queue_delay_p95_ms": random.randint(800, 3000) if degraded else random.randint(10, 200),
            "cpu_pct": round(random.uniform(70, 95), 1) if degraded else round(random.uniform(10, 60), 1),
            "error_rate": round(random.uniform(0.2, 0.6), 3) if degraded else round(random.uniform(0.0, 0.02), 3),
            "restarts_total": 0,
        }
