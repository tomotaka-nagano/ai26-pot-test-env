"""Domain models shared by the MCP service, store, and worker."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.EXPIRED,
})


@dataclass(frozen=True)
class DomainError:
    code: str
    message: str
    details: Any = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TestJob:
    test_id: str
    owner_id: str
    status: JobStatus
    submitted_at: float
    sequence: int
    started_at: float | None
    completed_at: float | None
    expires_at: float | None
    request_json: dict[str, Any] | None
    result_json: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    source_set_id: str | None = None
    phase: str | None = None

    def status_dict(self) -> dict[str, Any]:
        result = {
            "test_id": self.test_id,
            "status": self.status.value,
            "submitted_at": self.submitted_at,
        }
        if self.started_at is not None:
            result["started_at"] = self.started_at
        if self.completed_at is not None:
            result["completed_at"] = self.completed_at
        if self.error_code is not None:
            result["error"] = {"code": self.error_code, "message": self.error_message}
        if self.source_set_id is not None:
            result["source_set_id"] = self.source_set_id
        if self.phase is not None:
            result["phase"] = self.phase
        return result