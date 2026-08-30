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
        start = cls.script.index("function renderFreshness()")
        cls.render = cls.script[start : cls.script.index("\n  }\n", start)]

    def test_the_stamp_names_the_field_it_came_from(self):
        self.assertIn("data.newest_event_at", self.render, "the stamp must be read, not derived")

    def test_the_stamp_does_no_arithmetic(self):
        """No operator that could turn stored values into a figure of its own."""
        body = re.sub(r'"[^"\n]*"', '""', self.render)
        body = re.sub(r"'[^'\n]*'", "''", body)
        for operator in (" + data.", " - ", " * ", " / ", "Date.now", "new Date"):
            self.assertNotIn(operator, body, f"{operator!r} would be a figure computed in the page")

    def test_the_wall_clock_never_stands_in_for_the_store(self):
        """The masthead ticker is gone: a live clock beside stale data reads as fresh."""
        self.assertNotIn("toISOString", self.script)
        self.assertNotIn('$("clock")', self.script)

    def test_the_stamp_carries_a_citation(self):
        self.assertIn("ingest-newest", self.render)
        self.assertIn('citeId !== "ingest-newest"', self.script)

    def test_the_orphaned_ingest_citations_went_with_the_band(self):
        """The band is gone, so the cites nothing reaches any more are gone too."""
        for cite in ("ingest-accepted", "ingest-refused", "ingest-watermark"):
            self.assertNotIn(cite, self.script, f"{cite} is cited from nothing on the board")

    def test_the_readings_the_band_showed_survive_in_the_one_drawer_left(self):
        """Removing the line must not lose what ingest_health measured."""
        for field in ("watermark", "lag_seconds", "lateness_grace_seconds", "accepted",
                      "dead_letter.count", "newest_by_kind"):
            self.assertIn(field, self.script, f"{field} must still be reachable in the citation")

    def test_the_lag_is_never_worded_as_a_wall_clock_age(self):
        """The one wrong reading of lag_seconds, forbidden in the page's words."""
        self.assertNotIn("ago", self.render)
        self.assertIn("not seconds since a record arrived", self.script)

    def test_duplicates_is_shown_as_unmeasured_rather_than_as_a_number(self):
        self.assertIn("not measured - ", self.script)

    def test_an_unreadable_endpoint_darkens_the_stamp_instead_of_going_stale(self):
        self.assertIn("data.unreadable", self.render)
        self.assertIn("could not be read from the store", self.render)

    def test_the_stamp_sits_in_the_masthead_and_the_band_is_gone(self):
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="fresh-v"', markup)
        self.assertLess(
            markup.index('id="fresh-v"'),
            markup.index('id="overview-board"'),
            "freshness belongs in the masthead, above the views and outside the money",
        )
        self.assertNotIn('class="prov"', markup)
        self.assertNotIn('id="prov-ingest"', markup)
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".fresh-dot", css)
        self.assertNotIn(".prov-ingest", css)
        self.assertNotIn(".prov-disclose", css)

    def test_the_simulated_data_statement_is_moved_and_not_deleted(self):
        """A stated assumption stays reachable behind a disclosure."""
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("simulated data produced by this project's simulator", markup)
        self.assertIn("<details", markup[markup.index("<footer") : markup.index("</footer>")])


class LocalTimeTests(unittest.TestCase):
    """Every instant a human reads is written in the viewer's zone, not raw UTC."""

    @classmethod
    def setUpClass(cls):
        cls.script = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_a_stored_instant_with_no_zone_is_read_as_utc(self):
        """The store writes UTC. Reading it as local wall time would shift it."""
        self.assertIn('if (!/(Z|[+-]\\d{2}:?\\d{2})$/.test(text)) text += "Z";', self.script)

    def test_the_formatting_helpers_every_caller_shares_render_locally(self):
        for helper in ("function localStamp(", "function localClock(", "function localDay(",
                       "function stampWords("):
            self.assertIn(helper, self.script)
        # fmt() is the helper the detail tab and the overview both go through.
        fmt = self.script[self.script.index("function fmt(value)"):]
        fmt = fmt[: fmt.index("\n  }\n")]
        self.assertIn("localStamp(value, true)", fmt)

    def test_the_window_phrase_names_the_readers_zone_rather_than_asserting_utc(self):
        phrase = self.script[self.script.index("function windowPhrase(window)"):]
        phrase = phrase[: phrase.index("\n  }\n")]
        self.assertIn("LOCAL_ZONE", phrase)
        self.assertNotIn('"UTC"', phrase)
        self.assertNotIn('" UTC"', phrase)

    def test_the_citation_drawer_still_shows_the_stored_string(self):
        """Provenance keeps the raw value; the local reading only sits beside it."""
        drawer = self.script[self.script.index("function openCite(citeId)"):]
        drawer = drawer[: drawer.index("\n  }\n")]
        self.assertIn("escapeHtml(pretty(row[1]))", drawer)
        self.assertIn("cite-local", drawer)


class LoadingStateTests(unittest.TestCase):
    """An empty board and a broken one must not look the same."""

    @classmethod
    def setUpClass(cls):
        cls.script = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "styles.css").read_text(encoding="utf-8")

    def test_the_first_paint_draws_the_board_in_its_own_shapes(self):
        for name in ("skeletonOverview", "skeletonQueue", "skeletonDetail", "skeletonEvidence"):
            self.assertIn("function " + name + "(", self.script)
        self.assertIn("Reading the store...", self.script)
        self.assertIn(".skel-card", self.css)

    def test_the_skeletons_are_painted_before_the_first_read(self):
        boot = self.script[self.script.index("  renderAskExamples();"):]
        self.assertLess(boot.index("paintLoading()"), boot.index("refresh()"))

    def test_a_refresh_never_blanks_a_populated_board(self):
        refresh = self.script[self.script.index("  function refresh() {"):]
        refresh = refresh[: refresh.index("\n  }\n")]
        self.assertIn("if (!state.loaded) paintLoadFailure", refresh)
        self.assertIn("setFetching(true)", refresh)
        self.assertIn("setFetching(false)", refresh)

    def test_a_failed_read_says_so_rather_than_spinning_forever(self):
        self.assertIn("function paintLoadFailure(", self.script)
        self.assertIn("The board could not read the store", self.script)
        self.assertIn(".loadfail", self.css)

    def test_the_in_flight_cue_cannot_shift_the_layout(self):
        """The dot is always in the masthead; a read only changes how it looks."""
        markup = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="fresh-dot"', markup)
        self.assertIn("body.is-fetching .fresh-dot", self.css)

    def test_the_trigger_and_the_ask_both_show_they_are_working(self):
        self.assertIn("judge-spin", self.script)
        self.assertIn("ask-spin", self.script)
        self.assertIn(".judge-spin", self.css)

    def test_motion_is_dropped_where_the_reader_asked_for_none(self):
        self.assertIn("prefers-reduced-motion", self.css)


if __name__ == "__main__":
    unittest.main()
