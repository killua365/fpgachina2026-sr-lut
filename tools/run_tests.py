"""Run software regressions from the organized tree; no board access."""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in (
    "board/host", "board/pynq", "src/ps", "src/reference", "sim/nexys",
    "sim/software/host", "sim/software/ps", "sim/software/nexys",
    "sim/software/reference")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--suite", choices=("controller", "launcher", "reference", "ps", "golden"), default="controller")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    if args.suite == "controller":
        from business_session_gate import TESTS
        suite = unittest.defaultTestLoader.loadTestsFromNames(TESTS)
    else:
        directory, pattern = {
            "launcher": ("sim/software/host", "test_demo_session.py"),
            "reference": ("sim/software/reference", "test_*.py"),
            "ps": ("sim/software/ps", "test_*.py"),
            "golden": ("data/golden/tests", "test_*.py"),
        }[args.suite]
        suite = unittest.defaultTestLoader.discover(str(ROOT / directory), pattern)
    with (args.out / "tests.txt").open("x", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    record = dict(passed=result.wasSuccessful() and not result.skipped,
                  executed_tests_passed=result.wasSuccessful(),
                  tests=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped),
                  skipped_cases=[dict(test=str(test), reason=reason) for test, reason in result.skipped],
                  hardware_access=False, suite=args.suite)
    (args.out / "result.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    print(json.dumps(record))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
