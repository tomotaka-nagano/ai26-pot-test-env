"""Unit tests for content-addressed source storage."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from mcp_server.source_models import SourceManifest
from mcp_server.source_store import SourceStore


class SourceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = SourceStore(root / "jobs.sqlite", root / "blobs")
        self.content = b"int answer() { return 42; }\n"
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        self.manifest = SourceManifest.model_validate({
            "base_revision": "v1",
            "files": [{
                "path": "src_cpp/answer.cpp",
                "sha256": self.sha256,
                "size_bytes": len(self.content),
            }],
        })

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_prepare_reports_only_missing_blobs_and_is_idempotent(self) -> None:
        first = self.store.prepare(self.manifest)
        second = self.store.prepare(self.manifest)

        self.assertEqual(first.source_set_id, second.source_set_id)
        self.assertEqual(self.store.missing_blobs(first.source_set_id), [{
            "sha256": self.sha256,
            "size_bytes": len(self.content),
        }])

        self.store.put_bytes(self.sha256, self.content)
        self.assertEqual(self.store.missing_blobs(first.source_set_id), [])

    def test_finalize_requires_every_blob(self) -> None:
        record = self.store.prepare(self.manifest)
        self.assertEqual(self.store.finalize(record.source_set_id).status, "pending")

        self.store.put_bytes(self.sha256, self.content)
        self.assertEqual(self.store.finalize(record.source_set_id).status, "ready")

    def test_rejects_undeclared_or_invalid_blob_content(self) -> None:
        self.store.prepare(self.manifest)

        with self.assertRaisesRegex(ValueError, "sha256"):
            self.store.put_bytes(self.sha256, b"x" * len(self.content))
        with self.assertRaisesRegex(ValueError, "not declared"):
            self.store.put_bytes("0" * 64, b"")


if __name__ == "__main__":
    unittest.main()