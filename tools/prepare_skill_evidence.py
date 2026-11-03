"""Refresh a small provenance bundle from existing immutable project evidence.

No SSH, board access, FPGA load, or replay of performance tests.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
from archive_store import sha

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill"
EVIDENCE = SKILL / "evidence"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    copies = {
        "board/analysis/2026-10-18/camera_r5_scan_result/result.json": "camera_r5_result.json",
        "delivery/render_fix_20261019_r1/implementation.json": "render_r11_implementation.json",
        "board/experiments/2026-10-19/render_fix_r11/arm_qualification.json": "render_r11_arm.json",
        "build/artifacts/pynq_x4/stage_40m_scale4_system_route_timing.rpt": "route_timing.rpt",
        "build/artifacts/pynq_x4/stage_40l_scale4_system_route_utilization.rpt": "route_utilization.rpt",
        "build/artifacts/pynq_x4/sr_lut_x4.hwh": "overlay.hwh",
    }
    indexed = [
        "src/ps/video_pipeline/frame_session.py",
        "src/ps/hdmi_display/engine.py",
        "board/pynq/hdmi_hardware_validation.py",
        "skill/requirements_mapping.md",
        "report/performance/camera_20fps_2026-10-18.md",
    ]
    target = EVIDENCE/"project"
    target.mkdir(parents=True, exist_ok=True)
    sources = []
    for name, filename in copies.items():
        source, dest = ROOT/name, target/filename
        location = name
        if source.is_file():
            shutil.copyfile(source, dest)
        else:
            found = False
            for archive_name in ("report/performance/camera_r5_evidence.zip", "report/validation/historical_validation.zip"):
                archive = ROOT/archive_name
                metadata = json.loads(archive.with_suffix(".json").read_text("utf-8"))
                if name not in {item["path"] for item in metadata["files"]}: continue
                assert sha(archive) == metadata["sha256"]
                with zipfile.ZipFile(archive) as bundle:
                    dest.write_bytes(bundle.read(name))
                location = archive_name + "!" + name
                found = True
                break
            if not found: raise FileNotFoundError(name)
        if source.is_file(): assert digest(source) == digest(dest)
        sources.append({"project_path": location, "original_project_path": name, "sha256": digest(dest),
                        "bundled_path": dest.relative_to(SKILL).as_posix(),
                        "evidence_type": "historical_project_record_not_new_measurement"})
    for name in indexed:
        sources.append({"project_path": name, "sha256": digest(ROOT/name),
                        "bundled_path": None, "evidence_type": "project_source_reference"})
    record = {"date": "2026-10-23", "source_project_head":
              subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "hardware_access": False, "sources": sources}
    (EVIDENCE/"source_index.json").write_text(json.dumps(record, indent=2, ensure_ascii=False)+"\n", "utf-8")
    config = {
        "files": [{"path": "project/"+filename, "sha256": digest(target/filename)}
                  for filename in copies.values()],
        "hwh": {"path": "project/overlay.hwh", "required_ips": ["axi_dma_0"], "dma_ip": "axi_dma_0"},
        "timing": "project/route_timing.rpt",
        "utilization": {"path": "project/route_utilization.rpt", "instance": "sr_lut_s40_dyn_wrapper"},
    }
    (EVIDENCE/"project_reports.json").write_text(json.dumps(config, indent=2)+"\n", "utf-8")
    print(json.dumps({"copied": len(copies), "indexed": len(sources), "hardware_access": False}))


if __name__ == "__main__":
    main()
