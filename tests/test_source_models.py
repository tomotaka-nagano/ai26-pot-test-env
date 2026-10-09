"""Unit tests for uploaded C++ source manifests."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from mcp_server.source_models import SourceManifest


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class SourceManifestTests(unittest.TestCase):
    def test_accepts_cpp_headers_bindings_and_cmake(self) -> None:
        manifest = SourceManifest.model_validate({
            "base_revision": "goma-1015-v1",
            "files": [
                {"path": "src_cpp/bindings.cpp", "sha256": HASH_A, "size_bytes": 42},
                {"path": "src_cpp/controller/pot_controller.h", "sha256": HASH_B, "size_bytes": 17},
                {"path": "CMakeLists.txt", "sha256": HASH_C, "size_bytes": 10},
            ],
            "deleted_paths": ["src_cpp/ui/unused.hpp"],
        })

        self.assertEqual(manifest.files[0].path, "src_cpp/bindings.cpp")

    def test_rejects_generated_and_evaluation_files(self) -> None:
        for path in ("build/Makefile", "pot_cpp.cp313-win_amd64.pyd", "sim/bridge.py", "../CMakeLists.txt"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                SourceManifest.model_validate({
                    "base_revision": "v1",
                    "files": [{"path": path, "sha256": HASH_A, "size_bytes": 1}],
                })

    def test_rejects_invalid_hash_and_case_collisions(self) -> None:
        with self.assertRaises(ValidationError):
            SourceManifest.model_validate({
                "base_revision": "v1",
                "files": [
                    {"path": "src_cpp/io/io.cpp", "sha256": "ABC", "size_bytes": 1},
                ],
            })

        with self.assertRaises(ValidationError):
            SourceManifest.model_validate({
                "base_revision": "v1",
                "files": [
                    {"path": "src_cpp/a.cpp", "sha256": HASH_A, "size_bytes": 1},
                    {"path": "src_cpp/b.cpp", "sha256": HASH_A, "size_bytes": 2},
                ],
            })

        with self.assertRaises(ValidationError):
            SourceManifest.model_validate({
                "base_revision": "v1",
                "files": [
                    {"path": "src_cpp/io/io.cpp", "sha256": HASH_A, "size_bytes": 1},
                    {"path": "src_cpp/IO/io.cpp", "sha256": HASH_B, "size_bytes": 1},
                ],
            })

    def test_digest_is_independent_of_manifest_order(self) -> None:
        first = SourceManifest.model_validate({
            "base_revision": "v1",
            "files": [
                {"path": "src_cpp/z.cpp", "sha256": HASH_A, "size_bytes": 1},
                {"path": "src_cpp/a.cpp", "sha256": HASH_B, "size_bytes": 2},
            ],
        })
        second = SourceManifest.model_validate({
            "base_revision": "v1",
            "files": list(reversed([entry.model_dump() for entry in first.files])),
        })

        self.assertEqual(first.source_set_digest, second.source_set_digest)


if __name__ == "__main__":
    unittest.main()