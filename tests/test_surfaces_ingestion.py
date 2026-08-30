"""The provenance line: `/api/ingestion` and what the board is allowed to do with it.

Deliberately its own file. `tests/test_surfaces.py` is being edited by another
contributor on the escalation ladder, and two people appending to one test file
is the one avoidable merge conflict on a night with a code freeze.

What these tests hold is W4's own hard rule: the surface holds no domain logic,
and every number it shows is read from W2 and cited, never recomputed. So the
endpoint must return the evidence tool's answer field for field, and the page
must not be able to invent a figure the store did not produce.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector import evidence, store  # noqa: E402
from surfaces import store as surfaces_store  # noqa: E402
from surfaces.server import SurfacesApp  # noqa: E402
from tests import synthetic  # noqa: E402

STATIC = ROOT / "surfaces" / "static"


class IngestionEndpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "clearwave.db"

    def app(self):
        return SurfacesApp(self.database)

    def seed(self):
        connection = store.connect(self.database)
        store.ingest(connection, synthetic.with_provider_incident())
        connection.close()

    def test_a_store_nothing_has_ingested_into_answers_rather_than_failing(self):
        """A board pointed at a fresh file must not 500 on the provenance line."""
        status, payload = self.app().handle("GET", "/api/ingestion")
        self.assertEqual(status, 200)
        self.assertEqual(payload["accepted"], 0)
        self.assertEqual(payload["rejected"], 0)
        self.assertIsNone(payload["newest_event_at"])

    def test_the_endpoint_returns_the_evidence_tool_answer_field_for_field(self):
        """The surface passes W2's answer through; it does not shape one."""
        self.seed()
        payload = self.app().handle("GET", "/api/ingestion")[1]
        connection = store.connect(self.database)
        self.addCleanup(connection.close)
        self.assertEqual(payload, evidence.answer("ingest_health", {}, connection))

    def test_the_figures_agree_with_the_rows_the_store_holds(self):
        self.seed()
        payload = self.app().handle("GET", "/api/ingestion")[1]
        connection = store.connect(self.database)
        self.addCleanup(connection.close)
        held = connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"]
        self.assertEqual(payload["accepted"], held)
        self.assertGreater(payload["accepted"], 0)

    def test_a_refused_record_reaches_the_board_with_its_reason(self):
        connection = store.connect(self.database)
        store.write_batch(connection, [("attempt", {"not": "a payment"})])
        connection.commit()
        connection.close()
        payload = self.app().handle("GET", "/api/ingestion")[1]
        self.assertEqual(payload["rejected"], 1)
        self.assertEqual(payload["dead_letter"]["count"], 1)
        self.assertTrue(payload["dead_letter"]["reasons"][0]["reason"])

    def test_the_endpoint_is_read_only(self):
        """Reading provenance must not create incident, escalation or call rows."""
        self.seed()
        connection = store.connect(self.database)
        self.addCleanup(connection.close)
        tables = lambda: {  # noqa: E731
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        before = tables()
        counts_before = {name: connection.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"] for name in before}
        self.app().handle("GET", "/api/ingestion")
        self.assertEqual(tables() - before, set(), "no new table is created by a read")
        for name, n in counts_before.items():
            self.assertEqual(
                connection.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"], n
            )

    def test_an_unknown_path_is_still_a_404(self):
        self.assertEqual(self.app().handle("GET", "/api/ingest")[0], 404)


class MeasurementSessionTests(unittest.TestCase):
    def test_it_opens_the_ingestion_tables_the_board_session_does_not(self):
        """`session` prepares the board's tables; the C2 tools read W2's."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "clearwave.db"
            with surfaces_store.measurement_session(database) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) AS n FROM attempt").fetchone()["n"], 0
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) AS n FROM dead_letter").fetchone()["n"], 0
                )


class PageDoesNotComputeTests(unittest.TestCase):
    """W4's rule, held as a test: a figure that appears only in the UI is a defect."""

    @classmethod
    def setUpClass(cls):
        cls.script = (STATIC / "app.js").read_text(encoding="utf-8")
        start = cls.script.index("function renderIngestion()")
        cls.render = cls.script[start : cls.script.index("\n  }\n", start)]

    def test_every_figure_on_the_strip_names_the_field_it_came_from(self):
        for field in ("data.accepted", "data.rejected", "dead.count", "data.newest_event_at", "data.watermark"):
            self.assertIn(field, self.render, f"{field} must be read, not derived")

    def test_the_strip_does_no_arithmetic(self):
        """No operator that could turn stored values into a figure of its own."""
        body = re.sub(r'"[^"\n]*"', '""', self.render)
        body = re.sub(r"'[^'\n]*'", "''", body)
        for operator in (" + data.", " - ", " * ", " / ", "Date.now", "new Date"):
            self.assertNotIn(operator, body, f"{operator!r} would be a figure computed in the page")

    def test_every_figure_carries_a_citation(self):
        for cite in ("ingest-accepted", "ingest-refused", "ingest-newest", "ingest-watermark"):
            self.assertIn(cite, self.render)
            self.assertIn('citeId === "' + cite + '"', self.script)

    def test_the_lag_is_never_worded_as_a_wall_clock_age(self):
        """The one wrong reading of lag_seconds, forbidden in the page's words."""
        self.assertNotIn("ago", self.render)
        self.assertIn("not seconds since a record arrived", self.script)

    def test_duplicates_is_shown_as_unmeasured_rather_than_as_a_number(self):
        self.assertIn("not measured - ", self.script)

    def test_an_unreadable_endpoint_darkens_the_line_instead_of_going_stale(self):
        self.assertIn("data.unreadable", self.render)
        self.assertIn("could not be read from the store", self.render)

    def test_the_strip_sits_in_the_provenance_frame_and_not_in_the_money(self):
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="prov-ingest"', markup)
        self.assertLess(
            markup.index('id="prov-ingest"'),
            markup.index('id="overview-board"'),
            "provenance belongs at the frame edge, above the views and outside the money",
        )
        self.assertIn(".prov-ingest", (STATIC / "styles.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
