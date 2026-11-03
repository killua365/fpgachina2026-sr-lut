"""Run the skill from a detached temporary copy with the requested interpreter."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT/"skill"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--label", default="isolated")
    args = parser.parse_args()
    if not re.fullmatch("[a-z0-9_-]+", args.label):
        raise ValueError("Invalid evidence label")
    destination = SKILL/"evidence"/args.label
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    commands = []
    with tempfile.TemporaryDirectory(prefix="pynq_skill_") as temp:
        detached = Path(temp)/"skill"
        shutil.copytree(SKILL, detached, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "out", "manifest.json"))
        destination.mkdir(parents=True, exist_ok=True)
        configs = ["examples/image_threshold.json", "examples/sensor_gain.json",
                   "evidence/project_reports.json"]
        operations = [(["run_checks.py", "--config", config, "--out", "out/"+name], name)
                      for config, name in zip(configs, ("image", "sensor", "project"))]
        operations.append((["tests/run_tests.py", "--out", "out/regression.json"], "regression"))
        operations.append((["-c", "import sys,run_checks; assert 'pynq' not in sys.modules; "
                                  "print('offline_import_without_pynq_passed')"], "offline_import"))
        for command, name in operations:
            process = subprocess.run([args.python, *command], cwd=detached, env=environment,
                                     text=True, encoding="utf-8", capture_output=True, timeout=90)
            commands.append({"command": ["python", *command], "exit_code": process.returncode,
                             "stdout": process.stdout, "stderr": process.stderr})
            if process.returncode:
                raise RuntimeError("Detached check failed: " + name + "\n" + process.stderr)
        for name in ("image", "sensor", "project"):
            shutil.copyfile(detached/"out"/name/"result.json", destination/(name+".json"))
        shutil.copyfile(detached/"out/regression.json", destination/"regression.json")
        environment_result = subprocess.check_output(
            [args.python, "-c", "import json,sys,numpy; print(json.dumps(dict("
             "python=sys.version,numpy=numpy.__version__,venv=sys.prefix!=sys.base_prefix)))"],
            text=True, encoding="utf-8", env=environment)
    source_hashes = {}
    for p in SKILL.rglob("*.py"):
        ast.parse(p.read_text("utf-8"), feature_version=(3, 10))
        source_hashes[p.relative_to(SKILL).as_posix()] = digest(p)
    result = {
        "date": "2026-10-20", "passed": True, "label": args.label,
        "hardware_access": False, "new_board_validation": False,
        "detached_copy": True, "original_project_imports_required": False,
        "python310_syntax_only_checked": True,
        "environment": json.loads(environment_result), "commands": commands,
        "source_sha256": source_hashes,
    }
    (destination/"qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", "utf-8")
    print(json.dumps({"passed": True, "label": args.label, "environment": result["environment"]}))


if __name__ == "__main__":
    main()
