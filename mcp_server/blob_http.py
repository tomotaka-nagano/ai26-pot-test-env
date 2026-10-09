"""Raw HTTP upload endpoint for declared content-addressed blobs."""

from __future__ import annotations

import tempfile
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_server.source_models import SHA256_PATTERN
from mcp_server.source_store import SourceStore


def create_blob_put_handler(source_store: SourceStore, max_blob_bytes: int):
    async def put_blob(request: Request) -> JSONResponse:
        sha256 = request.path_params["sha256"]
        if not SHA256_PATTERN.fullmatch(sha256):
            return _error(400, "INVALID_BLOB_HASH", "The URL must contain a lowercase SHA-256 hash.")

        expected_size = source_store.expected_blob_size(sha256)
        if expected_size is None:
            return _error(404, "UNDECLARED_BLOB", "The blob is not declared by a source manifest.")
        if expected_size > max_blob_bytes:
            return _error(413, "BLOB_TOO_LARGE", "The declared blob exceeds the upload size limit.")
        if source_store.has_blob(sha256, expected_size):
            return JSONResponse({"ok": True, "sha256": sha256, "size_bytes": expected_size}, status_code=200)

        total_size = 0
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=source_store.blob_directory, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                async for chunk in request.stream():
                    total_size += len(chunk)
                    if total_size > max_blob_bytes or total_size > expected_size:
                        return _error(413, "BLOB_TOO_LARGE", "The uploaded blob exceeds its declared size or server limit.")
                    temporary.write(chunk)
            source_store.commit_blob(sha256, temporary_path, total_size)
        except ValueError as error:
            return _error(400, "BLOB_VALIDATION_FAILED", str(error))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return JSONResponse({"ok": True, "sha256": sha256, "size_bytes": total_size}, status_code=201)

    return put_blob


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message, "retryable": False}},
        status_code=status_code,
    )