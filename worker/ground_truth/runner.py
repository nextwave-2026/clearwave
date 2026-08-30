"""Runs one guaranteed scenario end to end: records the C6 injected
configuration on start, counts what the worker actually emits for the
scoped cohort while it runs, and records the C6 observation on close.

This is the only code that writes worker/ground_truth/store.py. W2 and W3
must never import this module - see docs/contracts/hidden-truth.md.
"""

from datetime import datetime, timedelta, timezone

from worker.ground_truth import store
from worker.ground_truth.scenarios import SCENARIOS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ScenarioRun:
    def __init__(self, scenario_id: str, duration_seconds: int = 900, db_path=None):
        if scenario_id not in SCENARIOS:
            raise ValueError(
                f"unknown scenario_id {scenario_id!r}, expected one of: {sorted(SCENARIOS)}"
            )
        self.definition = SCENARIOS[scenario_id]
        self.incident = self.definition.build_incident()
        self.payments_total = 0
        self.payments_approved = 0
        self._closed = False

        self._conn = store.connect(db_path)
        start = _now()
        end = start + timedelta(seconds=duration_seconds)
        self.instance_id = store.record_injection(
            self._conn,
            scenario_id=self.definition.scenario_id,
            scenario_name=self.definition.scenario_name,
            affected_cohort=self.incident.scope,
            failure_mode=self.definition.failure_mode,
            strength=self.definition.strength,
            start_time=_iso(start),
            end_time=_iso(end),
            event_time_bucket_seconds=duration_seconds,
        )

    def observe(self, attempts: list[dict], closed: dict) -> None:
        """Feed one finished payment chain (all its attempts, plus its
        payments.closed event). Cohort membership is decided by the first
        attempt - the one the incident actually targeted, before any
        reroute - and success by the chain's final outcome. Payment-level,
        not attempt-level: counting every retry as its own "attempted
        payment" is exactly the collapse docs/contracts/canonical-event.md
        forbids, and it would silently inflate volume for any cohort with
        retries.
        """
        if not self.incident.matches(attempts[0]):
            return
        self.payments_total += 1
        if closed["outcome"] == "approved":
            self.payments_approved += 1

    def close(self) -> None:
        if self._closed:
            return
        baseline = self.definition.strength_baseline
        observed_rate = (
            self.payments_approved / self.payments_total if self.payments_total else baseline
        )
        magnitude = {
            "metric": "payment_approval_conversion",
            "baseline": baseline,
            "observed": round(observed_rate, 4),
            "absolute_delta": round(observed_rate - baseline, 4),
            "attempted_payments": self.payments_total,
        }
        observed = {
            "affected_cohorts": [
                {"relationship": "direct", "cohort": self.incident.scope, "magnitude": magnitude}
            ],
            "aggregate_magnitude": magnitude,
        }
        evaluation = {
            "confounded": self.incident.confound_bank is not None,
            "priority_relations": [],
        }
        try:
            store.record_observation(self._conn, self.instance_id, observed, evaluation)
        finally:
            self._conn.close()
            self._closed = True
