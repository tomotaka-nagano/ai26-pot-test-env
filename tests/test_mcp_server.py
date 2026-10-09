"""Smoke tests for the MCP server registration and HTTP application."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_server.server import RULES_PATH, RULES_URI, create_http_app, create_server


class McpServerTests(unittest.TestCase):
    def test_creates_streamable_http_application_and_keeps_rules_unmodified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            server = create_server(Path(temporary_directory) / "jobs.sqlite")
            application = server.streamable_http_app(streamable_http_path="/mcp")

        self.assertIsNotNone(application)
        self.assertEqual(RULES_URI, "pot-sim://docs/test-case-rules")
        self.assertTrue(RULES_PATH.read_text(encoding="utf-8").startswith("# テストシナリオ JSON"))

    def test_combined_http_application_exposes_mcp_and_blob_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            application = create_http_app(root / "jobs.sqlite", root / "blobs")

        routes = {route.path for route in application.routes}
        self.assertIn("/mcp", routes)
        self.assertIn("/blobs/{sha256}", routes)


if __name__ == "__main__":
    unittest.main()