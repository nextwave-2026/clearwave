"""OpenAI environment loading and secret redaction, without network or a live key."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from investigation.env import (
    MISSING_KEY_MESSAGE,
    REDACTED,
    api_key_present,
    load_dotenv,
    openai_client_kwargs,
    openai_max_output_tokens,
    openai_reasoning_effort,
    parse_env_file,
    redact_secrets,
)
from investigation.vertical import (
    MISSING_KEY_MESSAGE as VERTICAL_MISSING_KEY_MESSAGE,
    format_report,
    investigate_store,
    main,
)
from investigation.vertical import VerticalOutcome


SECRET = "sk-test-leak-me-now-please-xyz"


class ParseEnvFileTests(unittest.TestCase):
    def test_parses_plain_quoted_and_export_lines(self):
        text = "\n".join(
            [
                "# comment",
                "",
                "OPENAI_API_KEY=plain",
                'OPENAI_MODEL="gpt-test"',
                "export OPENAI_BASE_URL='https://example.invalid/v1'",
            ]
        )
        self.assertEqual(
            parse_env_file(text),
            {
                "OPENAI_API_KEY": "plain",
                "OPENAI_MODEL": "gpt-test",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
            },
        )


class LoadDotenvTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / ".env"
        self.path.write_text(
            "OPENAI_API_KEY=from-file\n"
            "OPENAI_MODEL=gpt-from-file\n"
            "OPENAI_BASE_URL=https://example.invalid/v1\n"
            "OPENAI_REASONING_EFFORT=medium\n"
            "OPENAI_MAX_OUTPUT_TOKENS=7000\n",
            encoding="utf-8",
        )

    def test_loads_from_dotenv_when_environment_is_empty(self):
        environ: dict[str, str] = {}
        applied = load_dotenv(self.path, environ=environ)
        self.assertEqual(applied["OPENAI_API_KEY"], "from-file")
        self.assertEqual(environ["OPENAI_API_KEY"], "from-file")
        self.assertEqual(environ["OPENAI_MODEL"], "gpt-from-file")
        self.assertEqual(environ["OPENAI_BASE_URL"], "https://example.invalid/v1")
        self.assertEqual(environ["OPENAI_REASONING_EFFORT"], "medium")
        self.assertEqual(environ["OPENAI_MAX_OUTPUT_TOKENS"], "7000")

    def test_environment_wins_over_dotenv(self):
        environ = {"OPENAI_API_KEY": "from-env"}
        applied = load_dotenv(self.path, environ=environ)
        self.assertNotIn("OPENAI_API_KEY", applied)
        self.assertEqual(environ["OPENAI_API_KEY"], "from-env")
        self.assertEqual(environ["OPENAI_MODEL"], "gpt-from-file")

    def test_loads_from_process_environment(self):
        saved = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "from-env"
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("OPENAI_API_KEY", saved)
                if saved is not None
                else os.environ.pop("OPENAI_API_KEY", None)
            )
        )
        self.assertTrue(api_key_present())
        self.assertEqual(openai_client_kwargs()["api_key"], "from-env")


class OpenAIOptionTests(unittest.TestCase):
    def test_reasoning_effort_is_optional_and_output_ceiling_is_positive(self):
        with mock.patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": " medium ", "OPENAI_MAX_OUTPUT_TOKENS": "7000"}):
            self.assertEqual(openai_reasoning_effort(), "medium")
            self.assertEqual(openai_max_output_tokens(6000), 7000)

    def test_empty_options_use_defaults(self):
        with mock.patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": "", "OPENAI_MAX_OUTPUT_TOKENS": ""}):
            self.assertIsNone(openai_reasoning_effort())
            self.assertEqual(openai_max_output_tokens(6000), 6000)

    def test_invalid_output_ceiling_is_rejected(self):
        with mock.patch.dict(os.environ, {"OPENAI_MAX_OUTPUT_TOKENS": "0"}):
            with self.assertRaisesRegex(ValueError, "OPENAI_MAX_OUTPUT_TOKENS"):
                openai_max_output_tokens(6000)


class MissingKeyTests(unittest.TestCase):
    def test_missing_key_message_names_variable_and_example(self):
        self.assertEqual(MISSING_KEY_MESSAGE, VERTICAL_MISSING_KEY_MESSAGE)
        self.assertIn("OPENAI_API_KEY", MISSING_KEY_MESSAGE)
        self.assertIn(".env.example", MISSING_KEY_MESSAGE)
        self.assertNotIn("Traceback", MISSING_KEY_MESSAGE)

    def test_forcing_the_model_without_a_key_raises_the_missing_key_message(self):
        saved = os.environ.pop("OPENAI_API_KEY", None)
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("OPENAI_API_KEY", saved)
                if saved is not None
                else None
            )
        )
        with self.assertRaises(RuntimeError) as raised:
            investigate_store(Path("/tmp/clearwave-missing-key.db"), use_model=True)
        self.assertEqual(str(raised.exception), MISSING_KEY_MESSAGE)

    def test_cli_missing_key_failure_is_one_line_and_has_no_traceback(self):
        saved = os.environ.pop("OPENAI_API_KEY", None)
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("OPENAI_API_KEY", saved)
                if saved is not None
                else None
            )
        )
        stderr = io.StringIO()
        with mock.patch("investigation.vertical.execute_vertical_path", side_effect=RuntimeError(MISSING_KEY_MESSAGE)):
            with mock.patch("sys.stderr", stderr):
                code = main(["--db", "/tmp/unused.db"])
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), MISSING_KEY_MESSAGE + "\n")
        self.assertNotIn("Traceback", stderr.getvalue())


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = SECRET
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._saved

    def test_key_never_appears_in_output_or_error_text(self):
        leaked = f"OpenAI auth failed for {SECRET}"
        redacted = redact_secrets(leaked)
        self.assertNotIn(SECRET, redacted)
        self.assertIn(REDACTED, redacted)
        self.assertFalse(SECRET.startswith(REDACTED))
        self.assertFalse(SECRET.endswith(REDACTED))

        outcome = VerticalOutcome(
            mode="model",
            api_key_present=True,
            database=Path("/tmp/unused.db"),
            detected_incidents=[],
            incident={
                "incident_id": "inc-test",
                "affected_cohort": {"provider": "provider-p2"},
                "change": {"metric": "payment_approval_conversion", "expected": 0.9, "actual": 0.5},
                "financial_impact": {
                    "gmv_at_risk": {"amount": 1, "currency": "USD"},
                    "loss_per_hour": {"amount": 1, "currency": "USD"},
                },
                "severity": "low",
            },
            lifecycle_after_detect="detected",
            lifecycle_after_investigate="diagnosed",
            result={
                "incident_id": "inc-test",
                "outcome": "agent_unavailable",
                "leading_hypothesis": {"statement": leaked},
                "diagnostic_confidence": "low",
                "competing_explanations": [],
                "missing_evidence": [],
                "recommended_next_action": {"action": "wait", "urgency": "later"},
            },
            trail=[],
        )
        report = format_report(outcome)
        self.assertNotIn(SECRET, report)
        self.assertIn(REDACTED, report)


if __name__ == "__main__":
    unittest.main()
