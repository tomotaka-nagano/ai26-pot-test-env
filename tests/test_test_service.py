"""Integration tests for the queued test service and worker."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from mcp_server.models import JobStatus
from mcp_server.service import TestService
from mcp_server.source_models import SourceManifest
from mcp_server.source_store import SourceStore
from mcp_server.store import JobStore
from mcp_server.worker import TestWorker


PASSING_CASE = {
    "name": "instant pass",
    "timeout": 10,
    "steps": [{
        "trigger": {"type": "time", "seconds": 0},
        "actions": [],
        "assertions": [{"field": "state", "op": "==", "expected": "idle"}],
    }],
}


class StubBuildPipeline:
    def __init__(self) -> None:
        self.source_set_ids: list[str] = []

    def execute(self, source_set_id, test_case, cancellation, set_phase):
        self.source_set_ids.append(source_set_id)
        set_phase("building")
        set_phase("testing")
        return {"suite_status": "passed", "scenarios": [], "build": {"source_set_id": source_set_id}}


class TestServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = JobStore(root / "jobs.sqlite")
        self.source_store = SourceStore(root / "jobs.sqlite", root / "blobs")
        self.pipeline = StubBuildPipeline()
        self.worker = TestWorker(self.store, build_pipeline=self.pipeline)
        self.service = TestService(
            self.store,
            self.worker,
            source_store=self.source_store,
            base_revision="baseline",
        )
        await self.worker.start()

    async def asyncTearDown(self) -> None:
        await self.worker.stop()
        self.temporary_directory.cleanup()

    async def test_submit_executes_and_returns_result(self) -> None:
        submitted = self.service.submit(PASSING_CASE)
        self.assertTrue(submitted["ok"])

        for _ in range(100):
            status = self.service.get_status(submitted["test_id"])
            if status["status"] == JobStatus.COMPLETED.value:
                break
            await asyncio.sleep(0.01)

        result = self.service.get_result(submitted["test_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["suite_status"], "passed")

    async def test_invalid_case_is_not_queued(self) -> None:
        response = self.service.submit({"name": "invalid"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "INVALID_SCHEMA")
        self.assertEqual(self.service.list_tests()["tests"], [])

    async def test_ready_source_set_runs_through_build_pipeline(self) -> None:
        source_set = self.source_store.prepare(SourceManifest(base_revision="baseline"))
        source_set = self.source_store.finalize(source_set.source_set_id)

        submitted = self.service.submit(PASSING_CASE, source_set.source_set_id)
        for _ in range(100):
            status = self.service.get_status(submitted["test_id"])
            if status["status"] == JobStatus.COMPLETED.value:
                break
            await asyncio.sleep(0.01)

        result = self.service.get_result(submitted["test_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.pipeline.source_set_ids, [source_set.source_set_id])
        self.assertEqual(result["result"]["build"]["source_set_id"], source_set.source_set_id)


if __name__ == "__main__":
    unittest.main()