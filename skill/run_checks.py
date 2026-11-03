"""Offline skill entry point. Never imports PYNQ, downloads FPGA or opens SSH."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import platform
import sys

import numpy as np

from checks.validation import (compare_arrays, completion_rate, local_path,
                               sha256, timing_summary, utilization_summary,
                               validate_hwh, verify_files)
from examples.mock_backend import MockBackend
from examples.reference import golden, make_input
from templates.session import BatchSession, array_contract


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def run_config(config_path, output):
    config_path = Path(config_path).resolve()
    config, base = load_json(config_path), config_path.parent
    result = {
        "schema": "pynq_skill_validation_v1",
        "date": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "hardware_access": False, "new_board_validation": False,
        "python": platform.python_version(), "numpy": np.__version__,
        "platform": platform.platform(), "config_sha256": sha256(config_path),
        "checks": {},
    }
    checks = result["checks"]
    if "example" in config:
        name, parameter = config["example"], config["parameter"]
        if type(parameter) is not int:
            raise ValueError("Example parameter must be an integer")
        if name == "image_threshold" and not 0 <= parameter <= 255:
            raise ValueError("Threshold must be 0..255")
        if name == "sensor_gain" and not -32768 <= parameter <= 32767:
            raise ValueError("Gain must fit int16")
        shape, dtype = array_contract(config["contract"]["input"])
        source = make_input(name, shape)
        if source.dtype != dtype:
            raise ValueError("Example input dtype mismatch")
        backend = MockBackend(name, parameter)
        with BatchSession(backend, config["contract"]) as session:
            actual, measurement = session.run(source)
        expected = golden(source, name, parameter)
        checks["golden"] = compare_arrays(actual, expected)
        # Deliberate corruption must be detected, not silently accepted.
        corrupted = actual.copy()
        corrupted.flat[-1] = 1 if int(actual.flat[-1]) != 1 else 0
        checks["corruption_detected"] = {"passed": not compare_arrays(corrupted, expected)["passed"]}
        result.update(example=name, execution="offline_simulated_dma",
                      measurement=measurement, lifecycle_events=backend.events)
        output.mkdir(parents=True, exist_ok=True)
        for filename, data in (("input", source), ("expected", expected), ("actual", actual)):
            np.save(output / (filename + ".npy"), data, allow_pickle=False)
    if "files" in config:
        checks["files"] = {"passed": True, **verify_files(base, config["files"])}
    if "hwh" in config:
        cfg = config["hwh"]
        checks["hwh"] = {"passed": True, **validate_hwh(
            local_path(base, cfg["path"]), cfg["required_ips"], cfg.get("dma_ip"))}
    if "golden" in config:
        cfg = config["golden"]
        checks["golden"] = compare_arrays(
            np.load(local_path(base, cfg["actual"]), allow_pickle=False),
            np.load(local_path(base, cfg["expected"]), allow_pickle=False),
            cfg.get("atol", 0.0), cfg.get("rtol", 0.0))
    if "timing" in config:
        checks["timing"] = timing_summary(local_path(base, config["timing"]).read_text("utf-8-sig"))
    if "utilization" in config:
        cfg = config["utilization"]
        usage = utilization_summary(local_path(base, cfg["path"]).read_text("utf-8-sig"),
                                    cfg["instance"])
        limits = cfg.get("limits", {})
        if any(key not in usage or type(value) not in (int, float) or
               not np.isfinite(value) or value < 0 for key, value in limits.items()):
            raise ValueError("Invalid resource limit")
        checks["utilization"] = {"passed": all(usage[k] <= v for k, v in limits.items()),
                                 "limits": limits, **usage}
    if "throughput" in config:
        cfg = config["throughput"]
        rate = completion_rate(load_json(local_path(base, cfg["events"])),
                               cfg["start_s"], cfg["end_s"], cfg["stage"], cfg["epoch"])
        target = cfg.get("minimum")
        if target is not None and (not np.isfinite(target) or target < 0):
            raise ValueError("Invalid throughput minimum")
        checks["throughput"] = {"passed": target is None or rate["units_per_second"] >= target,
                                "minimum": target, **rate}
    if not checks:
        raise ValueError("No checks configured")
    result["passed"] = all(v["passed"] for v in checks.values())
    result["not_requested"] = sorted(set(("files", "hwh", "golden", "timing",
                                          "utilization", "throughput")) - checks.keys())
    return result


def write_report(output, result):
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")
    lines = ["# PYNQ Skill 检查结果", "", f"- 通过：{result['passed']}",
             "- 本轮硬件访问：否；不构成新板卡验收。", "",
             "```json", json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), "```", ""]
    (output / "report.md").write_text("\n".join(lines), "utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run_config(args.config, args.out)
    except Exception as exc:
        result = {"passed": False, "hardware_access": False,
                  "error_type": type(exc).__name__, "error": str(exc)}
    write_report(args.out, result)
    print(json.dumps({"passed": result["passed"], "hardware_access": False,
                      "report": str(args.out / "result.json")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
