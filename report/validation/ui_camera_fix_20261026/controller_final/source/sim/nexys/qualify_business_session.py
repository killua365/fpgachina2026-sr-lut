"""Freeze the incremental N5 controller, loader and source tests without boards."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'board/host'), str(ROOT/'src/ps'), str(ROOT/'sim/nexys'), str(ROOT/'board/pynq')] + [str(ROOT/p) for p in ('src/reference','tools','sim/software/ps','sim/software/host','sim/software/nexys','sim/software/reference')]
import business_session_gate as gate
from longrun_process import powershell


def main():
    out = Path(sys.argv[1]).resolve()
    out.relative_to(ROOT/'report/validation')
    (out/'source').mkdir(parents=True, exist_ok=False)
    scratch = ROOT/'build/n5_session_tmp'
    scratch.mkdir(exist_ok=True)
    tempfile.tempdir = str(scratch)
    inputs = []
    for name in gate.INPUTS:
        path = ROOT/name
        target = out/'source'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        inputs.append(dict(path=name, sha256=gate.sha(path)))
    suite = unittest.defaultTestLoader.loadTestsFromNames(gate.TESTS)
    with (out/'tests.txt').open('x', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = dict(passed=result.wasSuccessful(), tests=result.testsRun, skipped=len(result.skipped),
                  failures=len(result.failures), errors=len(result.errors), inputs=inputs, contract=gate.CONTRACT,
                  hardware_access=False, n5_passed=False, n6_passed=False, formal_release=False)
    script = "$ErrorActionPreference='Stop'; $taskTokens=$null; $taskErrors=$null; "
    script += "[System.Management.Automation.Language.Parser]::ParseFile('" + str(ROOT/'board/host/run_business_session.ps1').replace("'", "''") + "',[ref]$taskTokens,[ref]$taskErrors) | Out-Null; if($taskErrors){throw ($taskErrors | Out-String)}; 'N5_LAUNCHER_PARSE_OK'"
    script += "; [System.Management.Automation.Language.Parser]::ParseFile('" + str(ROOT/'board/host/capture_bridge_matrix_uart.ps1').replace("'", "''") + "',[ref]$taskTokens,[ref]$taskErrors) | Out-Null; if($taskErrors){throw ($taskErrors | Out-String)}; 'N5_CAPTURE_PARSE_OK'"
    with (out/'powershell_parse.txt').open('x', encoding='utf-8') as stream:
        parsed = subprocess.run([powershell(), '-NoProfile', '-NonInteractive', '-Command', script],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=30)
    report['powershell_parse_passed'] = parsed.returncode == 0
    report['passed'] = report['passed'] and report['powershell_parse_passed']
    for item in inputs:
        if gate.sha(ROOT/item['path']) != item['sha256']:
            report['passed'] = False
            report['source_changed_during_test'] = item['path']
    (out/'result.json').write_bytes((json.dumps(report, indent=2)+'\n').encode())
    files = [dict(path=p.relative_to(out).as_posix(), size=p.stat().st_size, sha256=gate.sha(p))
             for p in sorted(out.rglob('*')) if p.is_file()]
    (out/'manifest.json').write_bytes((json.dumps(dict(files=files), indent=2)+'\n').encode())
    print(json.dumps({k:v for k, v in report.items() if k not in ('inputs', 'contract')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
