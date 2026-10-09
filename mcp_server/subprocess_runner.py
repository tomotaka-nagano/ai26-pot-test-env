"""Child-process entry point that loads one native artifact and runs one suite."""

from __future__ import annotations

import json
import importlib.util
import os
import sys
from pathlib import Path


REQUIRED_BINDINGS = (
    "ISensorInput", "IActuatorOutput", "IDutyCycleOutput", "PotController",
    "PotState", "KeepWarmMode", "ErrorDetector", "KitchenTimer", "Buzzer", "Display",
)


def main() -> int:
    artifact_directory = Path(os.environ["POT_CPP_ARTIFACT_DIR"]).resolve()
    try:
        artifacts = list(artifact_directory.glob("pot_cpp*.pyd"))
        if len(artifacts) != 1:
            raise RuntimeError("artifact directory must contain exactly one pot_cpp extension")
        spec = importlib.util.spec_from_file_location("pot_cpp", artifacts[0])
        if spec is None or spec.loader is None:
            raise RuntimeError("could not create an import spec for pot_cpp")
        pot_cpp = importlib.util.module_from_spec(spec)
        sys.modules["pot_cpp"] = pot_cpp
        spec.loader.exec_module(pot_cpp)

        loaded_path = Path(pot_cpp.__file__).resolve()
        if loaded_path.parent != artifact_directory:
            raise RuntimeError(f"loaded pot_cpp from unexpected path: {loaded_path}")
        missing = [name for name in REQUIRED_BINDINGS if not hasattr(pot_cpp, name)]
        if missing:
            raise RuntimeError(f"missing required bindings: {', '.join(missing)}")

        from sim.scenario_runner import ScenarioSuiteRunner

        request = json.load(sys.stdin)
        result = ScenarioSuiteRunner.run_suite_sync(request)
        json.dump(result, sys.stdout, ensure_ascii=False)
        return 0
    except Exception as error:
        json.dump({
            "error_code": "BINDING_CONTRACT_ERROR",
            "error_message": str(error),
        }, sys.stdout, ensure_ascii=False)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())