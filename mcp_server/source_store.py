"""Persistent content-addressed blobs and immutable source sets."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from mcp_server.source_models import SourceManifest


@dataclass(frozen=True)
class SourceSetRecord:
    source_set_id: str
    status: str
    manifest: SourceManifest
    created_at: float
    finalized_at: float | None


class SourceStore:
    def __init__(self, database_path: str | Path, blob_directory: str | Path) -> None:
        self.database_path = str(database_path)
        self.blob_directory = Path(blob_directory)
        self.blob_directory.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS blobs (
                    sha256 TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_sets (
                    source_set_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    finalized_at REAL
                );
                CREATE TABLE IF NOT EXISTS source_files (
                    source_set_id TEXT NOT NULL REFERENCES source_sets(source_set_id),
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    PRIMARY KEY(source_set_id, path)
                );
                CREATE INDEX IF NOT EXISTS idx_source_files_sha256
                    ON source_files(sha256);
            """)

    @staticmethod
    def _to_record(row: sqlite3.Row | None) -> SourceSetRecord | None:
        if row is None:
            return None
        return SourceSetRecord(
            source_set_id=row["source_set_id"],
            status=row["status"],
            manifest=SourceManifest.model_validate(json.loads(row["manifest_json"])),
            created_at=row["created_at"],
            finalized_at=row["finalized_at"],
        )

    def prepare(self, manifest: SourceManifest) -> SourceSetRecord:
        source_set_id = manifest.source_set_digest
        now = time.time()
        manifest_json = json.dumps(manifest.canonical_dict(), separators=(",", ":"), sort_keys=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for entry in manifest.files:
                existing = connection.execute(
                    "SELECT size_bytes FROM source_files WHERE sha256 = ? LIMIT 1", (entry.sha256,)
                ).fetchone()
                if existing is not None and int(existing["size_bytes"]) != entry.size_bytes:
                    connection.execute("ROLLBACK")
                    raise ValueError("sha256 was previously declared with a different size")
            connection.execute(
                "INSERT OR IGNORE INTO source_sets(source_set_id, status, manifest_json, created_at) VALUES (?, 'pending', ?, ?)",
                (source_set_id, manifest_json, now),
            )
            for entry in manifest.files:
                connection.execute(
                    "INSERT OR IGNORE INTO source_files(source_set_id, path, sha256, size_bytes) VALUES (?, ?, ?, ?)",
                    (source_set_id, entry.path, entry.sha256, entry.size_bytes),
                )
            row = connection.execute(
                "SELECT * FROM source_sets WHERE source_set_id = ?", (source_set_id,)
            ).fetchone()
            connection.execute("COMMIT")
        record = self._to_record(row)
        if record is None:
            raise RuntimeError("source set was not persisted")
        return record

    def get(self, source_set_id: str) -> SourceSetRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_sets WHERE source_set_id = ?", (source_set_id,)
            ).fetchone()
        return self._to_record(row)

    def missing_blobs(self, source_set_id: str) -> list[dict[str, int | str]]:
        with self._connection() as connection:
            rows = connection.execute("""
                SELECT DISTINCT sf.sha256, sf.size_bytes
                FROM source_files sf
                LEFT JOIN blobs b ON b.sha256 = sf.sha256 AND b.size_bytes = sf.size_bytes
                WHERE sf.source_set_id = ? AND b.sha256 IS NULL
                ORDER BY sf.sha256
            """, (source_set_id,)).fetchall()
        return [{"sha256": row["sha256"], "size_bytes": row["size_bytes"]} for row in rows]

    def expected_blob_size(self, sha256: str) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT size_bytes FROM source_files WHERE sha256 = ? LIMIT 1", (sha256,)
            ).fetchone()
        return None if row is None else int(row["size_bytes"])

    def has_blob(self, sha256: str, size_bytes: int | None = None) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT size_bytes FROM blobs WHERE sha256 = ?", (sha256,)
            ).fetchone()
        if row is None or not self.blob_path(sha256).is_file():
            return False
        return size_bytes is None or int(row["size_bytes"]) == size_bytes

    def blob_path(self, sha256: str) -> Path:
        return self.blob_directory / sha256

    def put_bytes(self, sha256: str, content: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=self.blob_directory, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        try:
            self.commit_blob(sha256, temporary_path, len(content))
        finally:
            temporary_path.unlink(missing_ok=True)

    def commit_blob(self, sha256: str, temporary_path: Path, size_bytes: int) -> None:
        expected_size = self.expected_blob_size(sha256)
        if expected_size is None:
            raise ValueError("blob was not declared by a source manifest")
        if size_bytes != expected_size:
            raise ValueError("blob size does not match the source manifest")
        with temporary_path.open("rb") as source:
            actual_hash = self._hash_stream(source)
        if actual_hash != sha256:
            raise ValueError("blob sha256 does not match its content")

        target = self.blob_path(sha256)
        if not target.exists():
            os.replace(temporary_path, target)
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO blobs(sha256, size_bytes, created_at) VALUES (?, ?, ?)",
                (sha256, size_bytes, time.time()),
            )

    @staticmethod
    def _hash_stream(source: BinaryIO) -> str:
        digest = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()

    def finalize(self, source_set_id: str) -> SourceSetRecord | None:
        record = self.get(source_set_id)
        if record is None:
            return None
        if self.missing_blobs(source_set_id):
            return record
        now = time.time()
        with self._connection() as connection:
            connection.execute(
                "UPDATE source_sets SET status = 'ready', finalized_at = ? WHERE source_set_id = ?",
                (now, source_set_id),
            )
        return self.get(source_set_id)