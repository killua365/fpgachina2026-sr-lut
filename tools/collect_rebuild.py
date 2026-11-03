"""Collect reproducible build reports without replacing the qualified FPGA images."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from archive_store import ROOT, sha

sys.path.insert(0, str(ROOT / "board/pynq"))
from hdmi_hardware_validation import parse_routed_timing, require_clean_timing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=("pynq_x2", "pynq_x4", "nexys"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    work, out = args.work.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    files = []
    if args.platform.startswith("pynq"):
        scale = int(args.platform[-1])
        reports = list((work / "source/board").rglob("*.rpt"))
        reports += list((work / "source/board").rglob("*recreated_bd.tcl"))
        files += [(p, "reports/" + p.name) for p in reports]
        files += [(work / "hardware_configuration.json", "hardware_configuration.json")]
        export = work / ("source/build/hdmi_x%d_1080p30_work/export" % scale)
        generated = [export / ("sr_lut_x%d.%s" % (scale, ext)) for ext in ("bit", "hwh", "xsa")]
        files += [(generated[1], "generated.hwh")]
        timing_path = next(p for p in reports if p.name.endswith("_system_route_timing.rpt"))
        config = json.loads((work / "hardware_configuration.json").read_text("utf-8"))
        assert config["passed"] is True
    else:
        reports = list((work / "route").glob("*.rpt"))
        files += [(p, "reports/" + p.name) for p in reports]
        generated = [work / "route/routed.dcp"]
        timing_path = next(p for p in reports if "timing" in p.name and "route" in p.name)
    timing = parse_routed_timing(timing_path.read_text("utf-8"))
    require_clean_timing(timing)
    files += [(work / "preparation.json", "preparation.json")]
    if args.log:
        files += [(args.log, "build.log")]
    for source, relative in files:
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        assert sha(source) == sha(destination)
    result = dict(passed=True, platform=args.platform, hardware_access=False,
                  default_images_replaced=False, timing=timing,
                  generated=[dict(path=p.relative_to(work).as_posix(), bytes=p.stat().st_size,
                                  sha256=sha(p)) for p in generated])
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    entries = [dict(path=p.relative_to(out).as_posix(), sha256=sha(p), bytes=p.stat().st_size)
               for p in sorted(out.rglob("*")) if p.is_file()]
    (out / "manifest.json").write_text(json.dumps(dict(files=entries), indent=2) + "\n", "utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
