"""SQLite-backed persistent FIFO queue and test-result store."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from mcp_server.models import JobStatus, TERMINAL_STATUSES, TestJob


class JobStore:
    def __init__(self, database_path: str | Path, retention_seconds: float = 24 * 60 * 60) -> None:
        self.database_path = str(database_path)
        self.retention_seconds = retention_seconds
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    test_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at REAL NOT NULL,
                    sequence INTEGER NOT NULL UNIQUE,
                    started_at REAL,
                    completed_at REAL,
                    expires_at REAL,
                    request_json TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    source_set_id TEXT,
                    phase TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                    ON jobs(status, sequence);
                CREATE INDEX IF NOT EXISTS idx_jobs_expiry
                    ON jobs(status, expires_at);
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_id TEXT NOT NULL REFERENCES jobs(test_id),
                    event_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    details_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_job_events_test_id
                    ON job_events(test_id, id);
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
            if "source_set_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN source_set_id TEXT")
            if "phase" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN phase TEXT")

    @staticmethod
    def _to_job(row: sqlite3.Row | None) -> TestJob | None:
        if row is None:
            return None
        return TestJob(
            test_id=row["test_id"], owner_id=row["owner_id"], status=JobStatus(row["status"]),
            submitted_at=row["submitted_at"], sequence=row["sequence"], started_at=row["started_at"],
            completed_at=row["completed_at"], expires_at=row["expires_at"],
            request_json=json.loads(row["request_json"]) if row["request_json"] else None,
            result_json=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"], error_message=row["error_message"],
            cancel_requested=bool(row["cancel_requested"]),
            source_set_id=row["source_set_id"], phase=row["phase"],
        )

    @staticmethod
    def _event(connection: sqlite3.Connection, test_id: str, event_type: str, now: float, details: Any = None) -> None:
        connection.execute(
            "INSERT INTO job_events(test_id, event_type, created_at, details_json) VALUES (?, ?, ?, ?)",
            (test_id, event_type, now, json.dumps(details) if details is not None else None),
        )

    def submit(
        self,
        test_case: dict[str, Any],
        owner_id: str = "anonymous",
        max_queued: int = 100,
        source_set_id: str | None = None,
    ) -> TestJob | None:
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            queued_count = connection.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (JobStatus.QUEUED.value,)).fetchone()[0]
            if queued_count >= max_queued:
                connection.execute("ROLLBACK")
                return None
            sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM jobs").fetchone()[0]
            test_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO jobs(test_id, owner_id, status, submitted_at, sequence, request_json, source_set_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (test_id, owner_id, JobStatus.QUEUED.value, now, sequence, json.dumps(test_case), source_set_id),
            )
            self._event(connection, test_id, "submitted", now)
            connection.execute("COMMIT")
        return self.get(test_id)

    def claim_next(self) -> TestJob | None:
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY sequence ASC LIMIT 1", (JobStatus.QUEUED.value,)
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            test_id = row["test_id"]
            connection.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE test_id = ? AND status = ?",
                (JobStatus.RUNNING.value, now, test_id, JobStatus.QUEUED.value),
            )
            self._event(connection, test_id, "claimed", now)
            updated = connection.execute("SELECT * FROM jobs WHERE test_id = ?", (test_id,)).fetchone()
            connection.execute("COMMIT")
            return self._to_job(updated)

    def set_phase(self, test_id: str, phase: str) -> None:
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE jobs SET phase = ? WHERE test_id = ? AND status = ?",
                (phase, test_id, JobStatus.RUNNING.value),
            )
            if updated.rowcount:
                self._event(connection, test_id, "phase_changed", now, {"phase": phase})
            connection.execute("COMMIT")

    def get(self, test_id: str) -> TestJob | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE test_id = ?", (test_id,)).fetchone()
            return self._to_job(row)

    def list_recent(self, limit: int = 50, status: JobStatus | None = None) -> list[TestJob]:
        with self._connection() as connection:
            if status is None:
                rows = connection.execute("SELECT * FROM jobs ORDER BY submitted_at DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY submitted_at DESC LIMIT ?", (status.value, limit)
                ).fetchall()
            return [self._to_job(row) for row in rows]

    def cancel(self, test_id: str) -> TestJob | None:
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE test_id = ?", (test_id,)).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            status = JobStatus(row["status"])
            if status is JobStatus.QUEUED:
                connection.execute("UPDATE jobs SET status = ?, completed_at = ?, expires_at = ? WHERE test_id = ?", (JobStatus.CANCELLED.value, now, now + self.retention_seconds, test_id))
                self._event(connection, test_id, "cancelled", now)
            elif status is JobStatus.RUNNING:
                connection.execute("UPDATE jobs SET cancel_requested = 1 WHERE test_id = ?", (test_id,))
                self._event(connection, test_id, "cancel_requested", now)
            updated = connection.execute("SELECT * FROM jobs WHERE test_id = ?", (test_id,)).fetchone()
            connection.execute("COMMIT")
            return self._to_job(updated)

    def finish(self, test_id: str, status: JobStatus, result: dict[str, Any] | None = None, error_code: str | None = None, error_message: str | None = None) -> TestJob | None:
        if status not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("finish requires a terminal execution status")
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM jobs WHERE test_id = ?", (test_id,)).fetchone()
            if row is None or row["status"] != JobStatus.RUNNING.value:
                connection.execute("COMMIT")
                return self.get(test_id)
            connection.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, expires_at = ?, result_json = ?, error_code = ?, error_message = ? WHERE test_id = ?",
                (status.value, now, now + self.retention_seconds, json.dumps(result) if result is not None else None, error_code, error_message, test_id),
            )
            self._event(connection, test_id, status.value, now, {"error_code": error_code} if error_code else None)
            updated = connection.execute("SELECT * FROM jobs WHERE test_id = ?", (test_id,)).fetchone()
            connection.execute("COMMIT")
            return self._to_job(updated)

    def recovery(self) -> int:
        now = time.time()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT test_id FROM jobs WHERE status = ?", (JobStatus.RUNNING.value,)).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE jobs SET status = ?, completed_at = ?, expires_at = ?, error_code = ?, error_message = ? WHERE test_id = ?",
                    (JobStatus.FAILED.value, now, now + self.retention_seconds, "SERVER_RESTARTED", "The server restarted while this test was running.", row["test_id"]),
                )
                self._event(connection, row["test_id"], "failed", now, {"error_code": "SERVER_RESTARTED"})
            connection.execute("COMMIT")
            return len(rows)

    def expire(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        statuses = tuple(status.value for status in TERMINAL_STATUSES if status is not JobStatus.EXPIRED)
        placeholders = ",".join("?" for _ in statuses)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT test_id FROM jobs WHERE status IN ({placeholders}) AND expires_at <= ?", (*statuses, now)
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE jobs SET status = ?, request_json = NULL, result_json = NULL, error_code = NULL, error_message = NULL WHERE test_id = ?",
                    (JobStatus.EXPIRED.value, row["test_id"]),
                )
                self._event(connection, row["test_id"], "expired", now)
            connection.execute("COMMIT")
            return len(rows)