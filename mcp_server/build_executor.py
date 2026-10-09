"""CMake build boundary for submitted C++ source sets."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildResult:
    artifact_directory: Path
    artifact_path: Path
    cache_hit: bool
    log: str


class BuildFailure(RuntimeError):
    def __init__(self, code: str, message: str, log: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.log = log


class LocalCMakeBuildExecutor:
    def __init__(self, cache_directory: str | Path, timeout_seconds: float = 120.0) -> None:
        self.cache_directory = Path(cache_directory)
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def build(self, source_directory: str | Path, source_set_digest: str) -> BuildResult:
        extension_suffix = sysconfig.get_config_var("EXT_SUFFIX")
        if not extension_suffix:
            raise BuildFailure("BUILD_ENVIRONMENT_ERROR", "Python extension suffix is unavailable.")
        fingerprint = self._toolchain_fingerprint(extension_suffix)
        build_digest = hashlib.sha256(f"{source_set_digest}:{fingerprint}".encode("utf-8")).hexdigest()
        artifact_directory = self.cache_directory / build_digest
        cached_artifact = artifact_directory / f"pot_cpp{extension_suffix}"
        if cached_artifact.is_file():
            return BuildResult(artifact_directory, cached_artifact, True, "")

        source_path = Path(source_directory).resolve()
        build_directory = source_path.parent / f"{source_path.name}-build"
        pybind11_directory = self._pybind11_cmake_directory()
        configure = [
            "cmake", "-S", str(source_path), "-B", str(build_directory),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DPYBIND11_FINDPYTHON=ON",
            f"-DPython_EXECUTABLE={sys.executable}",
            f"-DPYTHON_EXECUTABLE={sys.executable}",
            f"-Dpybind11_DIR={pybind11_directory}",
        ]
        build = ["cmake", "--build", str(build_directory), "--config", "Release"]
        logs: list[str] = []
        self._run(configure, source_path, logs)
        self._run(build, source_path, logs)

        matches = list(build_directory.rglob(f"pot_cpp{extension_suffix}"))
        if len(matches) != 1:
            raise BuildFailure(
                "BUILD_ARTIFACT_MISSING",
                "The build did not produce exactly one compatible pot_cpp extension.",
                self._trim_log("\n".join(logs)),
            )
        artifact_directory.mkdir(parents=True, exist_ok=True)
        temporary_artifact = artifact_directory / f".{cached_artifact.name}.tmp"
        shutil.copy2(matches[0], temporary_artifact)
        temporary_artifact.replace(cached_artifact)
        return BuildResult(artifact_directory, cached_artifact, False, self._trim_log("\n".join(logs)))

    def _run(self, command: list[str], cwd: Path, logs: list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = "".join(part for part in (error.stdout, error.stderr) if isinstance(part, str))
            raise BuildFailure("BUILD_TIMEOUT", "The CMake build exceeded its deadline.", self._trim_log(output)) from error
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        logs.append(output)
        if completed.returncode != 0:
            raise BuildFailure("BUILD_FAILED", "The CMake build failed.", self._trim_log("\n".join(logs)))

    @staticmethod
    def _pybind11_cmake_directory() -> str:
        completed = subprocess.run(
            [sys.executable, "-m", "pybind11", "--cmakedir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return completed.stdout.strip()

    @staticmethod
    def _toolchain_fingerprint(extension_suffix: str) -> str:
        return "|".join((sys.platform, sys.version, extension_suffix, "cmake-release-v1"))

    @staticmethod
    def _trim_log(log: str, limit: int = 64 * 1024) -> str:
        return log[-limit:]