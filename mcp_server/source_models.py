"""Strict models for content-addressed C++ source submissions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BASE_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})
ALLOWED_SOURCE_SUFFIXES = frozenset({".cpp", ".h", ".hpp"})


def normalize_source_path(value: str) -> str:
    if not value or "\\" in value:
        raise ValueError("path must be a non-empty POSIX-style relative path")

    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must be normalized and cannot traverse directories")
    if any(part.rstrip(" .") != part for part in path.parts):
        raise ValueError("path segments cannot end with a space or period")
    if any(part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES for part in path.parts):
        raise ValueError("path contains a Windows reserved name")

    normalized = path.as_posix()
    if normalized == "CMakeLists.txt":
        return normalized
    if len(path.parts) < 2 or path.parts[0] != "src_cpp" or path.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
        raise ValueError("path is outside the allowed C++ source set")
    return normalized


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceFileEntry(StrictModel):
    path: str
    sha256: str
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_source_path(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


class SourceManifest(StrictModel):
    base_revision: str
    files: list[SourceFileEntry] = Field(default_factory=list, max_length=1000)
    deleted_paths: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("base_revision")
    @classmethod
    def validate_base_revision(cls, value: str) -> str:
        if not BASE_REVISION_PATTERN.fullmatch(value):
            raise ValueError("base_revision contains unsupported characters")
        return value

    @field_validator("deleted_paths")
    @classmethod
    def validate_deleted_paths(cls, values: list[str]) -> list[str]:
        return [normalize_source_path(value) for value in values]

    @model_validator(mode="after")
    def validate_unique_paths(self) -> SourceManifest:
        file_paths = [entry.path for entry in self.files]
        all_paths = file_paths + self.deleted_paths
        folded_paths = [path.casefold() for path in all_paths]
        if len(folded_paths) != len(set(folded_paths)):
            raise ValueError("paths must be unique and cannot differ only by case")
        sizes_by_hash: dict[str, int] = {}
        for entry in self.files:
            previous_size = sizes_by_hash.setdefault(entry.sha256, entry.size_bytes)
            if previous_size != entry.size_bytes:
                raise ValueError("files with the same sha256 must have the same size")
        return self

    def canonical_dict(self) -> dict[str, object]:
        return {
            "base_revision": self.base_revision,
            "files": [
                entry.model_dump()
                for entry in sorted(self.files, key=lambda item: item.path)
            ],
            "deleted_paths": sorted(self.deleted_paths),
        }

    @property
    def source_set_digest(self) -> str:
        payload = json.dumps(
            self.canonical_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()