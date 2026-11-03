"""Exercise empty, reused, incomplete and damaged offline caches without board access."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cache = out / "cache"
    env = dict(os.environ, SR_LUT_CACHE_DIR=str(cache))
    environments = json.loads((ROOT / "runtime/environments.json").read_text("utf-8"))["environments"]
    records = []

    def probe(mode):
        started = time.monotonic()
        code = "import sys,numpy,cv2,PIL,json; print(json.dumps(dict(python=sys.executable,numpy=numpy.__version__,opencv=cv2.__version__,pillow=PIL.__version__)))"
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "tools/initialize_runtime.ps1"), mode, "-c", code],
            cwd=out, env=env, text=True, encoding="utf-8", capture_output=True, timeout=300)
        assert process.returncode == 0, process.stdout + process.stderr
        value = json.loads(process.stdout.strip().splitlines()[-1])
        Path(value["python"]).resolve().relative_to(cache)
        return dict(environment=value, seconds=time.monotonic()-started)

    for mode in ("host", "reference"):
        destination = cache / (mode + "-" + environments[mode]["sha256"][:16])
        # Simulate interruption before the verified cache becomes available.
        destination.mkdir(parents=True)
        (destination / "incomplete.txt").write_text("interrupted extraction", "ascii")
        first = probe(mode)
        assert not (destination / "incomplete.txt").exists()
        marker = destination / ".complete.json"
        marker_time = marker.stat().st_mtime_ns
        second = probe(mode)
        assert marker.stat().st_mtime_ns == marker_time, "Valid runtime was needlessly extracted again"
        target = destination / "Lib/json/__init__.py"
        original = target.read_bytes()
        target.write_bytes(original + b"\n# simulated corruption\n")
        repaired = probe(mode)
        assert target.read_bytes() == original
        records.append(dict(mode=mode, interrupted_cache_recovered=True,
                            cache_reused=True, corruption_repaired=True,
                            initial=first, reused=second, repaired=repaired))
    assert records[0]["initial"]["environment"]["numpy"] == "2.2.6"
    assert records[1]["initial"]["environment"]["numpy"] == "1.26.4"
    record = dict(passed=True, hardware_access=False, checks=records)
    (out / "result.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    print(json.dumps(dict(passed=True, environments=2, hardware_access=False)))


if __name__ == "__main__":
    main()
