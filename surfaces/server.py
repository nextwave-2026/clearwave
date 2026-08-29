"""Read-only localhost HTTP server for the W4 dashboard."""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from detector.store import database_path as shared_database_path

from . import inject, present, store

STATIC_DIR = Path(__file__).resolve().parent / "static"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class SurfacesApp:
    """JSON application over the shared store. Safe to call without HTTP."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else shared_database_path()

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

    def trigger(self) -> dict[str, Any]:
        return inject.fire_hidden_incident()

    def pending_calls(self) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            return {"calls": store.list_pending_calls(connection)}

    def acknowledge_call(self, incident_id: str) -> dict[str, Any]:
        with store.session(self.db_path) as connection:
            return {"acknowledged": store.acknowledge_call(connection, incident_id)}

    def handle(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        del body
        if method == "GET" and path == "/api/overview":
            return 200, self.overview()
        if method == "GET" and path == "/api/incidents":
            return 200, self.queue()
        if method == "GET" and path == "/api/merchants":
            return 200, self.merchants()
        if method == "GET" and path == "/api/calls":
            return 200, self.pending_calls()
        if method == "GET" and path.startswith("/api/incidents/"):
            incident_id = path[len("/api/incidents/") :].strip("/")
            if not incident_id:
                return 404, {"error": "missing incident_id"}
            payload = self.detail(incident_id)
            if payload is None:
                return 404, {"error": "incident not found", "incident_id": incident_id}
            return 200, payload
        if method == "POST" and path in {"/api/trigger", "/api/judge/trigger"}:
            return 200, self.trigger()
        if method == "POST" and path.startswith("/api/calls/") and path.endswith("/ack"):
            incident_id = path[len("/api/calls/") : -len("/ack")].strip("/")
            return 200, self.acknowledge_call(incident_id)
        return 404, {"error": "not found", "path": path}


class SurfacesHandler(BaseHTTPRequestHandler):
    server_version = "ClearwaveSurfaces/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._send_json(*self._app().handle("GET", parsed.path, None))
            return
        self._send_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body: dict[str, Any] | None = None
        if raw:
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid json"})
                return
            body = decoded if isinstance(decoded, dict) else {}
        self._send_json(*self._app().handle("POST", parsed.path, body))

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
        if not str(candidate).startswith(str(STATIC_DIR.resolve())) or not candidate.is_file():
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
