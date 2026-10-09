"""Tests for applying source sets over the fixed baseline."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from mcp_server.source_models import SourceManifest
from mcp_server.source_store import SourceStore
from mcp_server.source_workspace import SourceWorkspaceAssembler


class SourceWorkspaceAssemblerTests(unittest.TestCase):
    def test_applies_changes_additions_and_deletions_without_mutating_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            base = root / "base"
            (base / "src_cpp" / "old").mkdir(parents=True)
            (base / "CMakeLists.txt").write_text("baseline cmake", encoding="utf-8")
            original = base / "src_cpp" / "old" / "remove.cpp"
            original.write_text("remove", encoding="utf-8")
            content = b"replacement"
            digest = hashlib.sha256(content).hexdigest()
            store = SourceStore(root / "data.sqlite", root / "blobs")
            manifest = SourceManifest.model_validate({
                "base_revision": "v1",
                "files": [{"path": "src_cpp/new/file.cpp", "sha256": digest, "size_bytes": len(content)}],
                "deleted_paths": ["src_cpp/old/remove.cpp"],
            })
            source_set = store.prepare(manifest)
            store.put_bytes(digest, content)
            source_set = store.finalize(source_set.source_set_id)

            assembled = SourceWorkspaceAssembler(base, store).assemble(source_set, root / "work")

            self.assertEqual((assembled / "src_cpp/new/file.cpp").read_bytes(), content)
            self.assertFalse((assembled / "src_cpp/old/remove.cpp").exists())
            self.assertTrue(original.exists())


if __name__ == "__main__":
    unittest.main()