"""Execute simulation tests in a fresh Python process per native artifact."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mcp_server.build_executor import BuildFailure


class TestProcessExecutor:
    def __init__(self, project_directory: str | Path, timeout_seconds: float = 60.0) -> None:
        self.project_directory = Path(project_directory).resolve()
        self.timeout_seconds = timeout_seconds

    def run(self, artifact_directory: str | Path, test_case: dict) -> dict:
        environment = os.environ.copy()
        environment["POT_CPP_ARTIFACT_DIR"] = str(Path(artifact_directory).resolve())
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "mcp_server.subprocess_runner"],
                cwd=self.project_directory,
                env=environment,
                input=json.dumps(test_case),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise BuildFailure("EXECUTION_TIMEOUT", "The test process exceeded its deadline.") from error
        if completed.returncode != 0:
            try:
                failure = json.loads(completed.stdout)
            except json.JSONDecodeError:
                failure = {}
            raise BuildFailure(
                failure.get("error_code", "EXECUTION_ERROR"),
                failure.get("error_message", "The isolated test process failed."),
                completed.stderr[-64 * 1024:],
            )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise BuildFailure("EXECUTION_ERROR", "The isolated test process returned invalid JSON.") from error