"""Unit tests for the persistent FIFO test-job store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_server.models import JobStatus
from mcp_server.store import JobStore


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.temporary_directory.name) / "jobs.sqlite", retention_seconds=1)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_claims_submitted_jobs_in_fifo_order(self) -> None:
        first = self.store.submit({"name": "first"})
        second = self.store.submit({"name": "second"})

        self.assertEqual(self.store.claim_next().test_id, first.test_id)
        self.assertEqual(self.store.claim_next().test_id, second.test_id)

    def test_queued_cancellation_is_immediate(self) -> None:
        job = self.store.submit({"name": "test"})
        cancelled = self.store.cancel(job.test_id)

        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
        self.assertIsNone(self.store.claim_next())

    def test_running_cancellation_sets_request_flag(self) -> None:
        job = self.store.submit({"name": "test"})
        claimed = self.store.claim_next()
        cancelled = self.store.cancel(job.test_id)

        self.assertEqual(claimed.status, JobStatus.RUNNING)
        self.assertTrue(cancelled.cancel_requested)

    def test_restart_marks_running_job_failed_and_keeps_queued_job(self) -> None:
        running = self.store.submit({"name": "running"})
        queued = self.store.submit({"name": "queued"})
        self.store.claim_next()

        self.assertEqual(self.store.recovery(), 1)
        self.assertEqual(self.store.get(running.test_id).error_code, "SERVER_RESTARTED")
        self.assertEqual(self.store.claim_next().test_id, queued.test_id)

    def test_expiry_drops_payload_but_keeps_tombstone(self) -> None:
        job = self.store.submit({"name": "test"})
        self.store.claim_next()
        completed = self.store.finish(job.test_id, JobStatus.COMPLETED, {"suite_status": "passed"})

        self.assertEqual(self.store.expire(completed.expires_at), 1)
        expired = self.store.get(job.test_id)
        self.assertEqual(expired.status, JobStatus.EXPIRED)
        self.assertIsNone(expired.result_json)

    def test_persists_source_set_and_execution_phase(self) -> None:
        job = self.store.submit({"name": "test"}, source_set_id="source-123")
        self.store.claim_next()
        self.store.set_phase(job.test_id, "building")

        running = self.store.get(job.test_id)
        self.assertEqual(running.source_set_id, "source-123")
        self.assertEqual(running.phase, "building")


if __name__ == "__main__":
    unittest.main()