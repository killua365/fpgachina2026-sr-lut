"""Package only the completed skill and verify its self-contained inventory."""
import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT/"skill"
OUTPUT = ROOT/"board/release/skill"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def included():
    return [p for p in sorted(SKILL.rglob("*")) if p.is_file()
            and "__pycache__" not in p.parts and p.suffix != ".pyc"
            and "out" not in p.relative_to(SKILL).parts and p.name != "manifest.json"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', dest='package_date', metavar='YYYYMMDD',
                        default=date.today().strftime('%Y%m%d'),
                        help='Package date; defaults to the current local date')
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'\d{8}', args.package_date):
            raise ValueError('Expected eight digits')
        package_date = datetime.strptime(args.package_date, '%Y%m%d').date()
    except ValueError:
        parser.error('--date must be a valid date in YYYYMMDD format')
    basename = 'pynq_skill_' + args.package_date
    archive = OUTPUT/(basename + '.zip')
    digest_path = OUTPUT/(basename + '.sha256')
    result_path = OUTPUT/(basename + '.json')
    if any(path.exists() for path in (archive, digest_path, result_path)):
        raise FileExistsError('Refuse to overwrite an existing dated Skill package: ' + basename)
    qualification = json.loads((SKILL/"evidence/qualification.json").read_text("utf-8"))
    if qualification.get("passed") is not True:
        raise ValueError("Skill is not qualified")
    expected_sources = qualification["source_sha256"]
    current_sources = {p.relative_to(SKILL).as_posix(): sha(p) for p in SKILL.rglob("*.py")}
    if expected_sources != current_sources:
        raise ValueError("Python sources changed after isolated qualification")
    entries = []
    broken = []
    for path in included():
        rel = path.relative_to(SKILL).as_posix()
        if path.name != "README.md" and not re.fullmatch(r"[a-z0-9_./-]+", rel):
            raise ValueError("Non-English filename: " + rel)
        if path.suffix == ".md":
            for link in re.findall(r"\]\(([^)]+)\)", path.read_text("utf-8")):
                if "://" in link or link.startswith("#"):
                    continue
                target = (path.parent / link.split("#")[0]).resolve()
                target.relative_to(SKILL.resolve())
                if not target.exists():
                    broken.append((rel, link))
        entries.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha(path)})
    if broken:
        raise ValueError("Broken local documentation links: " + repr(broken))
    manifest = {"schema": "pynq_skill_manifest_v1", "files": entries}
    (SKILL/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", "utf-8")
    sys.path.insert(0, str(SKILL))
    from checks.validation import verify_files
    verify_files(SKILL, entries)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        for entry in entries:
            bundle.write(SKILL/entry["path"], "skill/"+entry["path"])
        bundle.write(SKILL/"manifest.json", "skill/manifest.json")
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        for entry in entries:
            assert hashlib.sha256(bundle.read("skill/"+entry["path"])).hexdigest() == entry["sha256"]
    digest_path.write_text(sha(archive)+"  "+archive.name+"\n", "ascii")
    record = {"date": package_date.isoformat(), "passed": True, "hardware_access": False,
              "archive": archive.relative_to(ROOT).as_posix(), "sha256": sha(archive),
              "files_in_zip": len(entries)+1, "bytes": archive.stat().st_size,
              "all_document_links_exist": True, "all_inventory_hashes_match": True,
              "new_board_validation": False, "demo_modified_or_restarted": False}
    result_path.write_text(json.dumps(record, indent=2)+"\n", "utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
