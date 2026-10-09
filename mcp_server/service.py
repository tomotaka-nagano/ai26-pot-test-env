"""Application service implementing the public test-job use cases."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from mcp_server.models import DomainError, JobStatus, TestJob
from mcp_server.source_models import SourceManifest
from mcp_server.source_store import SourceStore
from mcp_server.store import JobStore
from mcp_server.worker import TestWorker
from sim.test_case_validator import validate_test_case


def _failure(error: DomainError) -> dict[str, Any]:
    return {"ok": False, "error": error.as_dict()}


class TestService:
    def __init__(
        self,
        store: JobStore,
        worker: TestWorker,
        max_queued: int = 100,
        source_store: SourceStore | None = None,
        base_revision: str = "baseline",
    ) -> None:
        self.store = store
        self.worker = worker
        self.max_queued = max_queued
        self.source_store = source_store
        self.base_revision = base_revision

    def validate(self, test_case: Any) -> dict[str, Any]:
        return validate_test_case(test_case)

    def prepare_source_upload(self, manifest_data: Any) -> dict[str, Any]:
        if self.source_store is None:
            return _failure(DomainError("SOURCE_UPLOAD_DISABLED", "Source upload is not configured."))
        try:
            manifest = SourceManifest.model_validate(manifest_data)
        except ValidationError as error:
            return _failure(DomainError("INVALID_SOURCE_MANIFEST", "The source manifest is invalid.", error.errors()))
        if manifest.base_revision != self.base_revision:
            return _failure(DomainError(
                "BASE_REVISION_MISMATCH",
                "The source manifest does not target the configured base revision.",
                {"expected": self.base_revision, "actual": manifest.base_revision},
            ))
        try:
            source_set = self.source_store.prepare(manifest)
        except ValueError as error:
            return _failure(DomainError("INVALID_SOURCE_MANIFEST", str(error)))
        return {
            "ok": True,
            "source_set_id": source_set.source_set_id,
            "status": source_set.status,
            "missing_blobs": self.source_store.missing_blobs(source_set.source_set_id),
        }

    def finalize_source_upload(self, source_set_id: str) -> dict[str, Any]:
        if self.source_store is None:
            return _failure(DomainError("SOURCE_UPLOAD_DISABLED", "Source upload is not configured."))
        source_set = self.source_store.get(source_set_id)
        if source_set is None:
            return _failure(DomainError("NOT_FOUND", "No source set exists for this source_set_id."))
        missing_blobs = self.source_store.missing_blobs(source_set_id)
        if missing_blobs:
            return _failure(DomainError(
                "SOURCE_UPLOAD_INCOMPLETE",
                "The source set is missing declared blobs.",
                {"missing_blobs": missing_blobs},
                retryable=True,
            ))
        finalized = self.source_store.finalize(source_set_id)
        return {"ok": True, "source_set_id": source_set_id, "status": finalized.status}

    def submit(self, test_case: Any, source_set_id: str | None = None) -> dict[str, Any]:
        validation = self.validate(test_case)
        if not validation["valid"]:
            return _failure(DomainError("INVALID_SCHEMA", "The test case is invalid.", validation["errors"]))
        if source_set_id is not None:
            if self.source_store is None:
                return _failure(DomainError("SOURCE_UPLOAD_DISABLED", "Source upload is not configured."))
            source_set = self.source_store.get(source_set_id)
            if source_set is None:
                return _failure(DomainError("NOT_FOUND", "No source set exists for this source_set_id."))
            if source_set.status != "ready":
                return _failure(DomainError("SOURCE_SET_NOT_READY", "The source set has not been finalized.", retryable=True))
        job = self.store.submit(
            validation["normalized_test_case"],
            max_queued=self.max_queued,
            source_set_id=source_set_id,
        )
        if job is None:
            return _failure(DomainError("QUEUE_FULL", "The test queue is full.", retryable=True))
        self.worker.wake()
        return {
            "ok": True,
            "test_id": job.test_id,
            "status": job.status.value,
            "submitted_at": job.submitted_at,
            "source_set_id": job.source_set_id,
        }

    def get_status(self, test_id: str) -> dict[str, Any]:
        self.store.expire()
        job = self.store.get(test_id)
        if job is None:
            return _failure(DomainError("NOT_FOUND", "No test exists for this test_id."))
        return {"ok": True, **job.status_dict()}

    def get_result(self, test_id: str) -> dict[str, Any]:
        self.store.expire()
        job = self.store.get(test_id)
        if job is None:
            return _failure(DomainError("NOT_FOUND", "No test exists for this test_id."))
        if job.status is JobStatus.EXPIRED:
            return _failure(DomainError("EXPIRED", "The retained test result has expired."))
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            return _failure(DomainError("RESULT_NOT_READY", "The test has not finished.", retryable=True))
        if job.status is JobStatus.CANCELLED:
            return _failure(DomainError("CANCELLED", "The test was cancelled."))
        if job.status is JobStatus.FAILED:
            return _failure(DomainError(job.error_code or "EXECUTION_ERROR", job.error_message or "The test failed before a result was produced."))
        return {"ok": True, "test_id": job.test_id, "status": job.status.value, "result": job.result_json}

    def cancel(self, test_id: str) -> dict[str, Any]:
        job = self.store.cancel(test_id)
        if job is None:
            return _failure(DomainError("NOT_FOUND", "No test exists for this test_id."))
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED}:
            return _failure(DomainError("INVALID_STATE", f"A {job.status.value} test cannot be cancelled."))
        self.worker.request_cancel(test_id)
        return {"ok": True, "test_id": test_id, "status": job.status.value, "cancel_requested": True}

    def list_tests(self, limit: int = 50, status: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            return _failure(DomainError("INVALID_SCHEMA", "limit must be between 1 and 100."))
        try:
            status_filter = JobStatus(status) if status is not None else None
        except ValueError:
            return _failure(DomainError("INVALID_SCHEMA", "status is not a supported job state."))
        self.store.expire()
        jobs = self.store.list_recent(limit, status_filter)
        return {"ok": True, "tests": [job.status_dict() for job in jobs]}