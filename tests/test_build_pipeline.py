"""Integration tests for rebuilding and loading submitted native sources."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mcp_server.build_executor import LocalCMakeBuildExecutor
from mcp_server.source_models import SourceManifest
from mcp_server.source_store import SourceStore
from mcp_server.source_workspace import SourceWorkspaceAssembler
from mcp_server.test_process import TestProcessExecutor


ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_SOURCE_DIR = Path(os.environ.get(
    "POT_MCP_BASE_SOURCE_DIR",
    ROOT_DIR.parent / "ai26-pot-application",
))
PASSING_CASE = {
    "name": "rebuilt baseline",
    "timeout": 10,
    "steps": [{
        "trigger": {"type": "time", "seconds": 0},
        "actions": [],
        "assertions": [{"field": "state", "op": "==", "expected": "idle"}],
    }],
}


class BuildPipelineTests(unittest.TestCase):
    @unittest.skipUnless(
        (BASE_SOURCE_DIR / "CMakeLists.txt").is_file() and (BASE_SOURCE_DIR / "src_cpp").is_dir(),
        "external pot application baseline is unavailable",
    )
    def test_builds_and_executes_baseline_in_fresh_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = SourceStore(root / "data.sqlite", root / "blobs")
            manifest = SourceManifest(base_revision="baseline")
            source_set = store.prepare(manifest)
            source_set = store.finalize(source_set.source_set_id)
            workspace = SourceWorkspaceAssembler(BASE_SOURCE_DIR, store).assemble(source_set, root / "source")

            build = LocalCMakeBuildExecutor(root / "artifacts").build(workspace, source_set.source_set_id)
            result = TestProcessExecutor(ROOT_DIR).run(build.artifact_directory, PASSING_CASE)
            cached = LocalCMakeBuildExecutor(root / "artifacts").build(workspace, source_set.source_set_id)

            self.assertFalse(build.cache_hit)
            self.assertTrue(cached.cache_hit)
            self.assertTrue(build.artifact_path.is_file())
            self.assertEqual(result["suite_status"], "passed")


if __name__ == "__main__":
    unittest.main()