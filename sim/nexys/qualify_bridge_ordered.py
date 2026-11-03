"""Freeze ordered-exit host qualification; never opens JTAG/UART/SSH."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'board/host'))
import bridge_ordered_gate as gate


def main():
    out=Path(sys.argv[1]).resolve(); out.relative_to(ROOT/'report/validation')
    (out/'source').mkdir(parents=True,exist_ok=False)
    inputs=[]
    for name in gate.INPUTS:
        src=ROOT/name; dst=out/'source'/name; dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst); inputs.append(gate.old.entry(src,name))
    result=dict(passed=False,hardware_access=False,contract=gate.CONTRACT,modules={},inputs=inputs)
    try:
        for module in gate.MODULES:
            with (out/(module+'.txt')).open('x',encoding='utf-8') as log:
                r=subprocess.run([sys.executable,'-X','utf8','-E','-B','-m','unittest','discover',
                                  '-s','sim/nexys','-p',module,'-v'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            result['modules'][module]=r.returncode
            if r.returncode: raise ValueError('Failed module: '+module)
        gate.old.exact(ROOT,inputs); result['passed']=True
    except BaseException as exc:
        result['error']=str(exc); raise
    finally:
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        files=[gate.old.entry(p,p.relative_to(out).as_posix()) for p in sorted(out.rglob('*')) if p.is_file()]
        (out/'manifest.json').write_text(json.dumps(dict(files=files),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(passed=True,modules=result['modules'])))


if __name__=='__main__': main()
