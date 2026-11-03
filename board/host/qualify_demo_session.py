"""Record offline launcher tests and exact input bytes in a new evidence directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest

from demo_session import ROOT, TOOLS, LAUNCHER_FILES, LAUNCHER_MANIFEST, write_new
from business_session_gate import SOURCE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.relative_to(ROOT / 'report/validation')
    manifest = ROOT / LAUNCHER_MANIFEST
    if manifest.exists():
        raise FileExistsError('Refuse to replace an existing launcher qualification manifest')
    out.mkdir(parents=True, exist_ok=False)
    inputs = {name: sha(ROOT / name) for name in LAUNCHER_FILES}
    started = time.monotonic()
    with (out / 'tests.txt').open('x', encoding='utf-8', newline='\n') as log:
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'sim/software/host'), pattern='test_demo_session.py')
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    unchanged = inputs == {name: sha(ROOT / name) for name in LAUNCHER_FILES}
    record = dict(schema='demo_launcher_offline_qualification_v1',
        recorded_utc=datetime.now(timezone.utc).isoformat(),
        baseline_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        passed=result.wasSuccessful() and not result.skipped and unchanged,
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        elapsed_seconds=time.monotonic()-started, inputs=inputs, inputs_unchanged=unchanged,
        test_log_sha256=sha(out/'tests.txt'), python=sys.version, platform=platform.platform(),
        actual_windows_processes=True, simulated_board_lifecycle=True, hardware_access=False,
        ssh_connected=False, fpga_programmed=False, services_changed=False,
        real_board_launcher_run=False, n5_passed=False, n6_passed=False, formal_release=False)
    write_new(out/'result.json',record)
    if not record['passed']:
        print(json.dumps(record,ensure_ascii=False))
        return 1
    manifest.parent.mkdir(parents=True, exist_ok=True)
    write_new(manifest,dict(schema='demo_launcher_manifest_v1',
        files=[dict(path=name,sha256=digest) for name,digest in inputs.items()],
        qualification=(out/'result.json').relative_to(ROOT).as_posix(),
        qualification_sha256=sha(out/'result.json'),
        session_tools=TOOLS.relative_to(ROOT).as_posix(),
        session_tool_manifest_sha256=sha(TOOLS/'manifest.json'),
        source_contract=SOURCE,
        source_contract_sha256=sha(ROOT/SOURCE),
        real_board_launcher_run=False, default_startup_changed=False, formal_release=False))
    print(json.dumps(dict(passed=True,tests=result.testsRun,qualification=str(out),manifest=str(manifest))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
