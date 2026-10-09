"""Streamable HTTP MCP entry point for queued pot-simulation tests."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_server.blob_http import create_blob_put_handler
from mcp_server.build_executor import LocalCMakeBuildExecutor
from mcp_server.build_pipeline import BuildAndTestPipeline
from mcp_server.service import TestService
from mcp_server.source_store import SourceStore
from mcp_server.source_workspace import SourceWorkspaceAssembler
from mcp_server.store import JobStore
from mcp_server.test_process import TestProcessExecutor
from mcp_server.worker import TestWorker


ROOT_DIR = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT_DIR / "doc" / "api" / "api.md"
RULES_URI = "pot-sim://docs/test-case-rules"
DEFAULT_BASE_SOURCE_DIR = ROOT_DIR.parent / "ai26-pot-application"


def _integer_setting(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def create_server(
    database_path: str | Path | None = None,
    blob_directory: str | Path | None = None,
) -> MCPServer:
    path = database_path or os.environ.get("POT_MCP_DB_PATH", ROOT_DIR / ".data" / "mcp-tests.sqlite")
    runtime_directory = Path(path).parent
    blobs = blob_directory or os.environ.get("POT_MCP_BLOB_DIR", runtime_directory / "blobs")
    store = JobStore(path, retention_seconds=_integer_setting("POT_MCP_RETENTION_SECONDS", 24 * 60 * 60))
    source_store = SourceStore(path, blobs)
    pipeline = BuildAndTestPipeline(
        source_store,
        SourceWorkspaceAssembler(
            os.environ.get("POT_MCP_BASE_SOURCE_DIR", DEFAULT_BASE_SOURCE_DIR),
            source_store,
        ),
        LocalCMakeBuildExecutor(
            os.environ.get("POT_MCP_ARTIFACT_CACHE_DIR", runtime_directory / "artifacts"),
            timeout_seconds=_integer_setting("POT_MCP_BUILD_TIMEOUT_SECONDS", 120),
        ),
        TestProcessExecutor(
            ROOT_DIR,
            timeout_seconds=_integer_setting("POT_MCP_EXECUTION_DEADLINE_SECONDS", 60),
        ),
        os.environ.get("POT_MCP_WORK_DIR", runtime_directory / "work"),
    )
    worker = TestWorker(
        store,
        execution_deadline_seconds=_integer_setting("POT_MCP_EXECUTION_DEADLINE_SECONDS", 60),
        build_pipeline=pipeline,
    )
    service = TestService(
        store,
        worker,
        max_queued=_integer_setting("POT_MCP_MAX_QUEUED", 100),
        source_store=source_store,
        base_revision=os.environ.get("POT_MCP_BASE_REVISION", "baseline"),
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer):
        await worker.start()
        try:
            yield {"service": service}
        finally:
            await worker.stop()

    server = MCPServer(
        "pot-simulation-tests",
        title="Pot Simulation Tests",
        description="Queues and executes JSON test cases in the pot simulator.",
        lifespan=lifespan,
    )

    @server.resource(RULES_URI, mime_type="text/markdown")
    def test_case_rules() -> str:
        return RULES_PATH.read_text(encoding="utf-8")

    @server.tool(structured_output=True)
    def validate_test_case(test_case: dict[str, Any]) -> dict[str, Any]:
        """Validate test-case JSON without placing it on the execution queue."""
        return service.validate(test_case)

    @server.tool(structured_output=True)
    def prepare_source_upload(manifest: dict[str, Any]) -> dict[str, Any]:
        """Validate a C++ source manifest and return blobs that need uploading."""
        return service.prepare_source_upload(manifest)

    @server.tool(structured_output=True)
    def finalize_source_upload(source_set_id: str) -> dict[str, Any]:
        """Finalize a source set after all declared blobs have been uploaded."""
        return service.finalize_source_upload(source_set_id)

    @server.tool(structured_output=True)
    def submit_test(test_case: dict[str, Any], source_set_id: str | None = None) -> dict[str, Any]:
        """Validate a test case, issue its UUID, and append it to the FIFO queue."""
        return service.submit(test_case, source_set_id)

    @server.tool(structured_output=True)
    def get_test_status(test_id: str) -> dict[str, Any]:
        """Return queued, running, completed, failed, cancelled, or expired status without progress."""
        return service.get_status(test_id)

    @server.tool(structured_output=True)
    def get_test_result(test_id: str) -> dict[str, Any]:
        """Return a completed test's result JSON or a structured availability error."""
        return service.get_result(test_id)

    @server.tool(structured_output=True)
    def cancel_test(test_id: str) -> dict[str, Any]:
        """Cancel a queued test immediately or request cooperative cancellation for a running test."""
        return service.cancel(test_id)

    @server.tool(structured_output=True)
    def list_tests(limit: int = 50, status: str | None = None) -> dict[str, Any]:
        """List the most recently submitted shared test jobs."""
        return service.list_tests(limit, status)

    return server


def create_http_app(
    database_path: str | Path | None = None,
    blob_directory: str | Path | None = None,
) -> Starlette:
    server = create_server(database_path, blob_directory)
    application = server.streamable_http_app(
        streamable_http_path="/mcp",
        max_request_body_size=_integer_setting("POT_MCP_MAX_REQUEST_BYTES", 1024 * 1024),
    )
    source_store = SourceStore(
        database_path or os.environ.get("POT_MCP_DB_PATH", ROOT_DIR / ".data" / "mcp-tests.sqlite"),
        blob_directory or os.environ.get("POT_MCP_BLOB_DIR", ROOT_DIR / ".data" / "blobs"),
    )
    application.routes.append(Route(
        "/blobs/{sha256}",
        create_blob_put_handler(source_store, _integer_setting("POT_MCP_MAX_BLOB_BYTES", 1024 * 1024)),
        methods=["PUT"],
    ))
    return application


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_http_app(),
        host=os.environ.get("POT_MCP_HOST", "127.0.0.1"),
        port=_integer_setting("POT_MCP_PORT", 8001),
    )


if __name__ == "__main__":
    main()