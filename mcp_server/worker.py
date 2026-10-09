"""Single-worker execution loop for persistent simulation jobs."""

from __future__ import annotations

import asyncio
import logging
from threading import Event

from mcp_server.build_executor import BuildFailure
from mcp_server.build_pipeline import BuildAndTestPipeline
from mcp_server.models import JobStatus
from mcp_server.store import JobStore


class TestWorker:
    def __init__(
        self,
        store: JobStore,
        execution_deadline_seconds: float = 60.0,
        build_pipeline: BuildAndTestPipeline | None = None,
    ) -> None:
        self.store = store
        self.execution_deadline_seconds = execution_deadline_seconds
        self.build_pipeline = build_pipeline
        self._wake_event = asyncio.Event()
        self._active_cancellations: dict[str, Event] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._logger = logging.getLogger(__name__)

    async def start(self) -> None:
        self.store.recovery()
        self.store.expire()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="simulation-test-worker")
        self.wake()

    async def stop(self) -> None:
        self._stopping = True
        for cancellation in self._active_cancellations.values():
            cancellation.set()
        self.wake()
        if self._task is not None:
            await self._task
            self._task = None

    def wake(self) -> None:
        self._wake_event.set()

    def request_cancel(self, test_id: str) -> None:
        cancellation = self._active_cancellations.get(test_id)
        if cancellation is not None:
            cancellation.set()
        self.wake()

    async def _run(self) -> None:
        while not self._stopping:
            self._wake_event.clear()
            claimed_any = await self._drain_queue()
            if not claimed_any:
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    self.store.expire()

    async def _drain_queue(self) -> bool:
        claimed_any = False
        while not self._stopping:
            job = self.store.claim_next()
            if job is None:
                return claimed_any
            claimed_any = True
            cancellation = Event()
            self._active_cancellations[job.test_id] = cancellation
            if job.cancel_requested:
                cancellation.set()
            try:
                if job.source_set_id is not None:
                    if self.build_pipeline is None:
                        raise BuildFailure("BUILD_DISABLED", "Source builds are not configured.")
                    result = await asyncio.to_thread(
                        self.build_pipeline.execute,
                        job.source_set_id,
                        job.request_json or {},
                        cancellation,
                        lambda phase: self.store.set_phase(job.test_id, phase),
                    )
                else:
                    self.store.set_phase(job.test_id, "testing")
                    from sim.scenario_runner import ScenarioSuiteRunner

                    result = await asyncio.to_thread(
                        ScenarioSuiteRunner.run_suite_sync,
                        job.request_json or {},
                        cancellation,
                        self.execution_deadline_seconds,
                    )
                status = JobStatus.CANCELLED if cancellation.is_set() or result.get("suite_status") == "stopped" else JobStatus.COMPLETED
                self.store.finish(job.test_id, status, result)
            except BuildFailure as error:
                status = JobStatus.CANCELLED if error.code == "CANCELLED" else JobStatus.FAILED
                self.store.finish(
                    job.test_id,
                    status,
                    error_code=error.code,
                    error_message=str(error),
                )
            except Exception:
                self._logger.exception("test execution failed", extra={"test_id": job.test_id})
                self.store.finish(
                    job.test_id,
                    JobStatus.FAILED,
                    error_code="EXECUTION_ERROR",
                    error_message="The simulation executor raised an unexpected error.",
                )
            finally:
                self._active_cancellations.pop(job.test_id, None)
        return claimed_any