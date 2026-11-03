"""Check startup and both offline interpreters from an independent English path."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    work = args.out.resolve()
    if not str(work).isascii() or work.exists():
        raise ValueError("Use a new, ASCII-only temporary verification directory")
    work.mkdir(parents=True)
    replica = work / "project"
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Git is required for the preserved source qualification")
    with (work / "clone.log").open("w", encoding="utf-8") as log:
        subprocess.run([git, "clone", "--no-hardlinks", "--no-checkout", str(ROOT), str(replica)],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    # Copy only startup dependencies, not a second complete project backup.
    directories = (
        "board", "src/ps", "src/reference", "sim/software", "sim/nexys", "tools",
        "build/artifacts", "build/candidates", "data/lut/original_npy", "src/rtl/nexys_bridge",
        "report/validation/reorganization_20261020",
        "report/validation/demo_launcher_20261020",
    )
    count = 0
    for directory in directories:
        for source in (ROOT / directory).rglob("*"):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            target = replica / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            count += 1
    for source in [ROOT / "demo.cmd", ROOT / "demo_vga.cmd", *(ROOT / "runtime").glob("*")]:
        if not source.is_file():
            continue
        target = replica / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        count += 1
    assert not (replica / ".git/objects/info/alternates").exists()
    assert not os.path.samefile(ROOT / "demo.cmd", replica / "demo.cmd")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["SR_LUT_CACHE_DIR"] = str(work / "cache")
    system = Path(os.environ["SystemRoot"]) / "System32"
    environment["PATH"] = os.pathsep.join((str(Path(git).parent), str(system),
                                          str(system / "WindowsPowerShell/v1.0")))
    commands = [
        ["cmd.exe", "/d", "/c", "demo.cmd", "--check"],
        ["cmd.exe", "/d", "/c", "demo_vga.cmd", "--check"],
        ["cmd.exe", "/d", "/c", "tools\\python.cmd", "host", "-c",
         "import json,sys,numpy,cv2,PIL;print(json.dumps(dict(python=sys.executable,numpy=numpy.__version__,opencv=cv2.__version__,pillow=PIL.__version__)))"],
        ["cmd.exe", "/d", "/c", "tools\\python.cmd", "reference", "-c",
         "import json,sys,numpy,scipy,cv2;print(json.dumps(dict(python=sys.executable,numpy=numpy.__version__,scipy=scipy.__version__,opencv=cv2.__version__)))"],
    ]
    records = []
    for index, command in enumerate(commands):
        outcome = subprocess.run(command, cwd=replica, env=environment,
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=600)
        (work / f"check_{index}.txt").write_text(outcome.stdout + outcome.stderr, "utf-8")
        records.append(dict(command=command, exit_code=outcome.returncode))
        if outcome.returncode:
            raise RuntimeError(f"Portable check {index} failed; see {work}")
    result = dict(passed=True, files_copied=count, independent_git=True,
                  isolated_cache=True, system_python_on_path=False, hardware_access=False,
                  scope="dp_and_vga_qualified_startup_and_both_offline_interpreters",
                  commands=records, temporary_work_directory=str(work))
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
