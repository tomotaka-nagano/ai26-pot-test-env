"""Orchestrate source assembly, native build, and isolated test execution."""

from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Event
from typing import Callable

from mcp_server.build_executor import BuildFailure, LocalCMakeBuildExecutor
from mcp_server.source_store import SourceStore
from mcp_server.source_workspace import SourceWorkspaceAssembler
from mcp_server.test_process import TestProcessExecutor


class BuildAndTestPipeline:
    def __init__(
        self,
        source_store: SourceStore,
        assembler: SourceWorkspaceAssembler,
        build_executor: LocalCMakeBuildExecutor,
        test_executor: TestProcessExecutor,
        work_directory: str | Path,
    ) -> None:
        self.source_store = source_store
        self.assembler = assembler
        self.build_executor = build_executor
        self.test_executor = test_executor
        self.work_directory = Path(work_directory)
        self.work_directory.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        source_set_id: str,
        test_case: dict,
        cancellation: Event,
        set_phase: Callable[[str], None],
    ) -> dict:
        source_set = self.source_store.get(source_set_id)
        if source_set is None or source_set.status != "ready":
            raise BuildFailure("SOURCE_SET_NOT_READY", "The source set is missing or not ready.")
        self._check_cancelled(cancellation)
        with tempfile.TemporaryDirectory(dir=self.work_directory) as temporary_directory:
            set_phase("assembling")
            source_directory = self.assembler.assemble(source_set, Path(temporary_directory) / "source")
            self._check_cancelled(cancellation)
            set_phase("building")
            build = self.build_executor.build(source_directory, source_set.source_set_id)
            self._check_cancelled(cancellation)
            set_phase("testing")
            result = self.test_executor.run(build.artifact_directory, test_case)
            result["build"] = {"cache_hit": build.cache_hit, "source_set_id": source_set_id}
            return result

    @staticmethod
    def _check_cancelled(cancellation: Event) -> None:
        if cancellation.is_set():
            raise BuildFailure("CANCELLED", "The source build or test was cancelled.")