"""Offline tests for the reachable-model discovery command."""

from __future__ import annotations

import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from investigation.models import discover_models, main
from investigation.env import MISSING_KEY_MESSAGE


class ModelDiscoveryTests(unittest.TestCase):
    def test_discover_models_returns_sorted_ids(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: SimpleNamespace(
                    data=[{"id": "z-model"}, {"id": "a-model"}, {"id": "z-model"}]
                )
            )
        )
        self.assertEqual(discover_models(client), ["a-model", "z-model"])

    def test_missing_key_prints_shared_message_without_constructing_client(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}), mock.patch(
            "investigation.models.load_dotenv"
        ), mock.patch("investigation.models.OpenAI") as openai, mock.patch(
            "sys.stdout", stdout
        ), mock.patch("sys.stderr", stderr):
            code = main()
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), MISSING_KEY_MESSAGE + "\n")
        openai.assert_not_called()

    def test_api_error_is_one_line_and_redacts_key(self):
        secret = "sk-model-discovery-secret"
        stdout = io.StringIO()
        stderr = io.StringIO()
        client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError(secret)))
        )
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": secret}), mock.patch(
            "investigation.models.load_dotenv"
        ), mock.patch("investigation.models.OpenAI", return_value=client), mock.patch(
            "sys.stdout", stdout
        ), mock.patch("sys.stderr", stderr):
            code = main()
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "investigation.models: [redacted]\n")
        self.assertNotIn(secret, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
