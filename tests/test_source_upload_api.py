"""API boundary tests for source upload preparation and blob transfer."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_server.blob_http import create_blob_put_handler
from mcp_server.service import TestService
from mcp_server.source_store import SourceStore
from mcp_server.store import JobStore


PASSING_CASE = {
    "name": "source-backed",
    "steps": [{
        "trigger": {"type": "time", "seconds": 0},
        "actions": [],
        "assertions": [{"field": "state", "op": "==", "expected": "idle"}],
    }],
}


class StubWorker:
    def __init__(self) -> None:
        self.wake_count = 0

    def wake(self) -> None:
        self.wake_count += 1

    def request_cancel(self, test_id: str) -> None:
        pass


class SourceUploadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.job_store = JobStore(root / "jobs.sqlite")
        self.source_store = SourceStore(root / "jobs.sqlite", root / "blobs")
        self.worker = StubWorker()
        self.service = TestService(
            self.job_store,
            self.worker,
            source_store=self.source_store,
            base_revision="baseline-v1",
        )
        self.content = b"// submitted source\n"
        self.sha256 = hashlib.sha256(self.content).hexdigest()
        self.manifest = {
            "base_revision": "baseline-v1",
            "files": [{
                "path": "src_cpp/submitted.cpp",
                "sha256": self.sha256,
                "size_bytes": len(self.content),
            }],
        }

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_prepare_upload_finalize_and_submit(self) -> None:
        prepared = self.service.prepare_source_upload(self.manifest)
        self.assertTrue(prepared["ok"])
        self.assertEqual(prepared["missing_blobs"][0]["sha256"], self.sha256)

        incomplete = self.service.finalize_source_upload(prepared["source_set_id"])
        self.assertEqual(incomplete["error"]["code"], "SOURCE_UPLOAD_INCOMPLETE")

        app = Starlette(routes=[Route(
            "/blobs/{sha256}", create_blob_put_handler(self.source_store, 1024), methods=["PUT"]
        )])
        with TestClient(app) as client:
            response = client.put(f"/blobs/{self.sha256}", content=self.content)
            self.assertEqual(response.status_code, 201)
            duplicate = client.put(f"/blobs/{self.sha256}", content=self.content)
            self.assertEqual(duplicate.status_code, 200)

        finalized = self.service.finalize_source_upload(prepared["source_set_id"])
        self.assertEqual(finalized["status"], "ready")
        submitted = self.service.submit(PASSING_CASE, prepared["source_set_id"])
        self.assertTrue(submitted["ok"])
        self.assertEqual(
            self.job_store.get(submitted["test_id"]).source_set_id,
            prepared["source_set_id"],
        )

    def test_rejects_wrong_base_revision_and_blob_content(self) -> None:
        invalid_manifest = {**self.manifest, "base_revision": "other"}
        response = self.service.prepare_source_upload(invalid_manifest)
        self.assertEqual(response["error"]["code"], "BASE_REVISION_MISMATCH")

        prepared = self.service.prepare_source_upload(self.manifest)
        app = Starlette(routes=[Route(
            "/blobs/{sha256}", create_blob_put_handler(self.source_store, 1024), methods=["PUT"]
        )])
        with TestClient(app) as client:
            response = client.put(f"/blobs/{self.sha256}", content=b"x" * len(self.content))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "BLOB_VALIDATION_FAILED")
        self.assertEqual(self.service.finalize_source_upload(prepared["source_set_id"])["ok"], False)


if __name__ == "__main__":
    unittest.main()