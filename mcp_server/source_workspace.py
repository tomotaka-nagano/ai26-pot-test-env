"""Assemble an immutable source set over the configured baseline."""

from __future__ import annotations

import shutil
from pathlib import Path

from mcp_server.source_store import SourceSetRecord, SourceStore


class SourceWorkspaceAssembler:
    def __init__(self, base_directory: str | Path, source_store: SourceStore) -> None:
        self.base_directory = Path(base_directory).resolve()
        self.source_store = source_store

    def assemble(self, source_set: SourceSetRecord, destination: str | Path) -> Path:
        if source_set.status != "ready":
            raise ValueError("source set must be ready before assembly")

        destination_path = Path(destination).resolve()
        destination_path.mkdir(parents=True, exist_ok=False)
        shutil.copy2(self.base_directory / "CMakeLists.txt", destination_path / "CMakeLists.txt")
        shutil.copytree(self.base_directory / "src_cpp", destination_path / "src_cpp")

        for deleted_path in source_set.manifest.deleted_paths:
            target = destination_path / Path(deleted_path)
            if target.is_file():
                target.unlink()

        for entry in source_set.manifest.files:
            target = destination_path / Path(entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.source_store.blob_path(entry.sha256), target)

        return destination_path