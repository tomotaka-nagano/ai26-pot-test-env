"""Build and load the external pot application baseline on demand."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType

from mcp_server.build_executor import LocalCMakeBuildExecutor


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE_SOURCE_DIR = ROOT_DIR.parent / "ai26-pot-application"


def ensure_pot_cpp_loaded() -> ModuleType:
    loaded = sys.modules.get("pot_cpp")
    if loaded is not None:
        return loaded

    base_directory = Path(os.environ.get(
        "POT_MCP_BASE_SOURCE_DIR",
        DEFAULT_BASE_SOURCE_DIR,
    )).resolve()
    _validate_base_directory(base_directory)
    source_digest = _source_digest(base_directory)
    runtime_directory = Path(os.environ.get("POT_NATIVE_RUNTIME_DIR", ROOT_DIR / ".data" / "native"))
    runtime_directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=runtime_directory) as temporary_directory:
        staged_source = Path(temporary_directory) / "source"
        staged_source.mkdir()
        shutil.copy2(base_directory / "CMakeLists.txt", staged_source / "CMakeLists.txt")
        shutil.copytree(base_directory / "src_cpp", staged_source / "src_cpp")
        build = LocalCMakeBuildExecutor(runtime_directory / "artifacts").build(
            staged_source,
            source_digest,
        )

    spec = importlib.util.spec_from_file_location("pot_cpp", build.artifact_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load native module from {build.artifact_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pot_cpp"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("pot_cpp", None)
        raise
    return module


def _validate_base_directory(base_directory: Path) -> None:
    if not (base_directory / "CMakeLists.txt").is_file() or not (base_directory / "src_cpp").is_dir():
        raise RuntimeError(
            "External pot application baseline is unavailable. "
            "Set POT_MCP_BASE_SOURCE_DIR to its project directory."
        )


def _source_digest(base_directory: Path) -> str:
    digest = hashlib.sha256()
    paths = [base_directory / "CMakeLists.txt"]
    paths.extend(sorted(
        path
        for path in (base_directory / "src_cpp").rglob("*")
        if path.is_file() and path.suffix.lower() in {".cpp", ".h", ".hpp"}
    ))
    for path in paths:
        relative_path = path.relative_to(base_directory).as_posix().encode("utf-8")
        digest.update(len(relative_path).to_bytes(4, "big"))
        digest.update(relative_path)
        digest.update(path.read_bytes())
    return digest.hexdigest()