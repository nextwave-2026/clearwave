"""Localhost HTTP server for the W4 dashboard.

Every read is read-only over the shared store: the dashboard renders what
detection and investigation already wrote and computes nothing of its own.

The one write is the judge trigger. `POST /api/trigger` publishes a developing,
collapse, or clear command to W1's incident-control topic through
`surfaces.inject`, which changes the behaviour of a running worker. It writes
nothing to the store - whatever the board later shows arrives the same way
every other finding does, by being detected in the traffic - but calling this
server read-only would be a lie, so it is not called that.
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from detector.store import database_path as shared_database_path
from investigation.env import load_dotenv

from . import escalation, inject, present, store

STATIC_DIR = Path(__file__).resolve().parent / "static"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
# Nothing this server accepts needs to be large; a huge or negative
# Content-Length is a malformed or hostile request, not a real payload.
MAX_BODY_BYTES = 1_000_000


class SurfacesApp:
    """JSON application over the shared store. Safe to call without HTTP."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else shared_database_path()
        self.injected = False
        self.stage = "clear"

    def overview(self) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            incidents = store.list_incidents(connection)
            investigations = _investigations(connection, incidents)
            for incident in incidents:
                stored = investigations.get(str(incident.get("incident_id")))
                store.ensure_escalation(connection, incident, stored)
            return present.overview(incidents, investigations)

    def queue(self) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            incidents = store.list_incidents(connection)
            investigations = _investigations(connection, incidents)
            for incident in incidents:
                stored = investigations.get(str(incident.get("incident_id")))
                store.ensure_escalation(connection, incident, stored)
            return present.queue(incidents, investigations)

    def merchants(self) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            incidents = store.list_incidents(connection)
            return {"merchants": present.merchant_health(incidents)}

    def detail(self, incident_id: str) -> dict[str, Any] | None:
        with store.session(self.db_path) as connection:
            incident = store.load_incident(connection, incident_id)
            if incident is None:
                return None
            investigation = store.load_investigation(connection, incident_id)
            events = store.ensure_escalation(connection, incident, investigation)
            return present.detail(incident, investigation, events)

    def trigger(self, active: bool = True, stage: str | None = None) -> dict[str, Any]:
        """Publish one judge stage, remembering only what we published.

        `self.stage` is this control's own record of its last successful
        command, which is what a reloaded page reflects. It is not a reading
        of the worker: nothing here interrogates W1, so a worker restarted
        underneath us is honestly outside what this can know.
        """
        resolved = inject.resolve_stage(active=active, stage=stage)
        outcome = inject.fire_hidden_incident(active, stage=resolved)
        if outcome.get("delivered"):
            self.stage = resolved
            self.injected = resolved != "clear"
        return {**outcome, "active": self.injected, "stage": self.stage}

    def trigger_state(self) -> dict[str, Any]:
        """What the control last published, for a page that has just loaded."""
        return inject.describe(stage=self.stage)

    def escalations(self) -> dict[str, Any]:
        """Every stored escalation outcome, with the binding it was routed on."""
        with store.session(self.db_path) as connection:
            incidents = store.list_incidents(connection)
            investigations = _investigations(connection, incidents)
            recorded: dict[str, list[dict[str, Any]]] = {}
            for incident in incidents:
                stored = investigations.get(str(incident.get("incident_id")))
                recorded[str(incident.get("incident_id"))] = store.ensure_escalation(
                    connection, incident, stored
                )
            calls = store.list_pending_calls(connection)
            # The picture of the binding is read from the same function escalate
            # routes on, so it cannot drift from the behaviour it describes.
            binding = {
                severity: escalation.channels_for(severity)
                for severity in present.SEVERITY_LADDER
            }
            payload = present.escalations(incidents, recorded, binding, calls)
            payload["slack_channel"] = (
                os.environ.get(escalation.SLACK_CHANNEL_ENV) or escalation.DEFAULT_SLACK_CHANNEL
            )
            return payload

    def pending_calls(self) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            return {"calls": store.list_pending_calls(connection)}

    def acknowledge_call(self, incident_id: str) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            return {"acknowledged": store.acknowledge_call(connection, incident_id)}

    def handle(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path in {"/api/trigger", "/api/judge/trigger"}:
            return 200, self.trigger_state()
        if method == "GET" and path == "/api/overview":
            return 200, self.overview()
        if method == "GET" and path == "/api/incidents":
            return 200, self.queue()
        if method == "GET" and path == "/api/merchants":
            return 200, self.merchants()
        if method == "GET" and path == "/api/calls":
            return 200, self.pending_calls()
        if method == "GET" and path == "/api/escalations":
            return 200, self.escalations()
        if method == "GET" and path.startswith("/api/incidents/"):
            incident_id = path[len("/api/incidents/") :].strip("/")
            if not incident_id:
                return 404, {"error": "missing incident_id"}
            payload = self.detail(incident_id)
            if payload is None:
                return 404, {"error": "incident not found", "incident_id": incident_id}
            return 200, payload
        if method == "POST" and path in {"/api/trigger", "/api/judge/trigger"}:
            # Stage and the legacy on/off boolean are the only intents that
            # cross this boundary. Any other key in the body - a scenario id
            # above all - is ignored by construction, because nothing else is
            # read out of it. A body-less POST, and `{active: true}`, still
            # mean the full break.
            requested = None if body is None else body.get("stage")
            stage = requested if requested in inject.STAGES else None
            active = True if body is None else bool(body.get("active", True))
            return 200, self.trigger(active, stage=stage)
        return 404, {"error": "not found", "path": path}


class SurfacesHandler(BaseHTTPRequestHandler):
    server_version = "ClearwaveSurfaces/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._send_json(*self._safe_handle("GET", parsed.path, None))
            return
        self._send_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        raw = self.rfile.read(length) if length else b""
        body: dict[str, Any] | None = None
        if raw:
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid json"})
                return
            body = decoded if isinstance(decoded, dict) else {}
        self._send_json(*self._safe_handle("POST", parsed.path, body))

    def _safe_handle(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        # This server is reachable during a live judge demo. An internal
        # exception (a SQLite lock timeout, an unexpectedly-shaped stored
        # record) must become a clean JSON error, not a broken connection
        # with a traceback on whatever console is projected.
        try:
            return self._app().handle(method, path, body)
        except Exception:  # noqa: BLE001 - never let a request crash the server
            return 500, {"error": "internal error"}

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("CLEARWAVE_SURFACES_QUIET"):
            return
        super().log_message(format, *args)

    def _app(self) -> SurfacesApp:
        return self.server.app  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        if not candidate.is_relative_to(STATIC_DIR.resolve()) or not candidate.is_file():
            self._send_json(404, {"error": "not found", "path": path})
            return
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(candidate.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_server(
    db_path: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), SurfacesHandler)
    httpd.app = SurfacesApp(db_path)
    return httpd


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Clearwave W4 surfaces server")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (localhost only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLEARWAVE_SURFACES_PORT", "8080")))
    parser.add_argument("--db", default=None, help="SQLite path; defaults to CLEARWAVE_DB")
    args = parser.parse_args(argv)
    httpd = make_server(args.db, host=args.host, port=args.port)
    print(f"Clearwave surfaces on http://{args.host}:{httpd.server_address[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def _investigations(connection: Any, incidents: list[dict[str, Any]]) -> dict[str, Any]:
    found = {}
    for incident in incidents:
        incident_id = str(incident.get("incident_id", ""))
        found[incident_id] = store.load_investigation(connection, incident_id)
    return found


if __name__ == "__main__":
    raise SystemExit(main())
