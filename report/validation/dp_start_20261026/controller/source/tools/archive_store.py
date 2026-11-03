"""Verified offline archive extraction into explicit workspaces or owned caches."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def cache_root():
    return Path(os.environ.get("SR_LUT_CACHE_DIR") or
                Path(os.environ["LOCALAPPDATA"]) / "sr_lut_fpga/cache").resolve()


def extract_checked(archive, destination, expected_sha, records=None):
    archive, destination = Path(archive), Path(destination).resolve()
    if sha(archive) != expected_sha:
        raise ValueError("Archive digest differs: " + str(archive))
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive members")
        for item in source.infolist():
            relative = PurePosixPath(item.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in item.filename:
                raise ValueError("Unsafe archive member: " + item.filename)
            target = (destination / item.filename).resolve()
            target.relative_to(destination)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as data, target.open("xb") as output:
                while chunk := data.read(1024 * 1024):
                    output.write(chunk)
    if records is not None:
        for item in records:
            target = (destination / item["path"]).resolve()
            target.relative_to(destination)
            if sha(target) != item["sha256"]:
                raise ValueError("Extracted member digest differs: " + item["path"])
    return destination


def component(name, destination):
    archive = ROOT / "src/third_party" / (name + ".zip")
    metadata = json.loads(archive.with_suffix(".json").read_text("utf-8"))
    return extract_checked(archive, destination, metadata["sha256"], metadata["files"])


def resolve_payload(contract, root=ROOT):
    """Legacy fixture directories remain supported; current payloads use an external cache."""
    if contract.get("directory") != "@cache/current_payload":
        return Path(root) / contract["directory"]
    expected = contract["package_sha256"]
    destination = cache_root() / ("payload-" + expected[:16])
    marker = destination / ".complete.json"
    if marker.is_file() and json.loads(marker.read_text("utf-8")).get("sha256") == expected:
        return destination
    if destination.exists():
        raise ValueError("Incomplete payload cache: " + str(destination))
    staging = destination.with_name(destination.name + "-" + uuid.uuid4().hex + ".partial")
    extract_checked(Path(root) / contract["archive"], staging, expected)
    manifest = json.loads((staging / "hdmi_business_package.json").read_text("utf-8"))
    if sha(staging / "hdmi_business_package.json") != contract["package_manifest_sha256"]:
        raise ValueError("Unexpected package manifest")
    for item in manifest["files"]:
        path = (staging / item["path"]).resolve()
        path.relative_to(staging)
        if sha(path) != item["sha256"]:
            raise ValueError("Unexpected payload member: " + item["path"])
    (staging / ".complete.json").write_text(json.dumps(dict(sha256=expected)) + "\n", "utf-8")
    staging.rename(destination)
    return destination
