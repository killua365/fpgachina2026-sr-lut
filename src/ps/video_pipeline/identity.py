"""Release and Overlay identity shared by every deployment boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


OVERLAY_LAYOUT = {
    "media_x2": "sr_lut_x2",
    "media_x4": "sr_lut_x4",
    "camera_x4": "sr_lut_x4",
}


class ReleaseIdentityError(RuntimeError):
    """Raised when release manifests or deployed Overlay bytes disagree."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseIdentityError(f"{label} is unreadable: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseIdentityError(f"{label} must contain a JSON object: {path}")
    return value


def _manifest_candidates(filename: str, override_name: str) -> tuple[Path, ...]:
    module = Path(__file__).resolve()
    override = os.environ.get(override_name)
    return tuple(
        path
        for path in (
            Path(override).expanduser() if override else None,
            module.parents[1] / "manifest" / filename,
            module.parents[3] / "board" / "recovery" / "legacy_manifest" / filename,
        )
        if path is not None
    )


def _load_first_manifest(
    filename: str, override_name: str, label: str
) -> tuple[dict[str, Any], Path]:
    candidates = _manifest_candidates(filename, override_name)
    for candidate in candidates:
        if candidate.is_file():
            return _read_json(candidate, label), candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise ReleaseIdentityError(f"{label} is missing; searched: {searched}")


def _load_hardware_manifest() -> tuple[dict[str, Any], Path]:
    packaged = _manifest_candidates(
        "hardware_release.json", "SR_LUT_HARDWARE_MANIFEST"
    )
    for candidate in packaged:
        if candidate.is_file():
            return _read_json(candidate, "hardware manifest"), candidate.resolve()
    return _load_first_manifest(
        "release.json", "SR_LUT_HARDWARE_MANIFEST", "hardware manifest"
    )


def _artifact_map(release: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReleaseIdentityError("hardware manifest has no artifacts array")
    mapped: dict[str, Mapping[str, Any]] = {}
    for entry in artifacts:
        if not isinstance(entry, dict) or not isinstance(entry.get("role"), str):
            raise ReleaseIdentityError("hardware manifest contains an invalid artifact")
        role = str(entry["role"])
        if role in mapped:
            raise ReleaseIdentityError(f"hardware manifest repeats artifact role {role}")
        mapped[role] = entry
    return mapped


def validate_manifest_contract(
    application: Mapping[str, Any], release: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    """Return expected Overlay identities after cross-manifest validation."""

    try:
        application_stage = int(application["hardware_stage"])
        release_stage = int(release["stage"])
        application_status = str(application["release_status"])
        release_status = str(release["release_status"])
        overlay_set = application["overlay_set"]
        frozen = application["frozen_overlay"]
    except (KeyError, TypeError, ValueError) as error:
        raise ReleaseIdentityError(f"release manifest identity is incomplete: {error}") from error
    if application_stage != release_stage:
        raise ReleaseIdentityError(
            f"application Stage {application_stage} != hardware Stage {release_stage}"
        )
    if application_status != release_status:
        raise ReleaseIdentityError(
            f"application status {application_status} != hardware status {release_status}"
        )
    if application_status not in {"formal", "candidate"}:
        raise ReleaseIdentityError(
            f"release status must be formal or candidate, got {application_status}"
        )
    if not isinstance(overlay_set, dict) or not isinstance(frozen, dict):
        raise ReleaseIdentityError("application Overlay declarations are invalid")

    artifacts = _artifact_map(release)
    expected: dict[str, dict[str, str]] = {}
    for component, stem in OVERLAY_LAYOUT.items():
        declared_directory = overlay_set.get(component)
        if not isinstance(declared_directory, str) or not declared_directory.replace(
            "\\", "/"
        ).endswith(f"/{component}"):
            raise ReleaseIdentityError(
                f"application overlay_set.{component} does not select {component}"
            )
        component_expected: dict[str, str] = {"stem": stem}
        for suffix, kind in (("bit", "bitstream"), ("hwh", "hardware_handoff")):
            role = f"{component}_{kind}"
            entry = artifacts.get(role)
            if entry is None:
                raise ReleaseIdentityError(f"hardware manifest is missing role {role}")
            digest = str(entry.get("sha256", "")).upper()
            path = str(entry.get("path", "")).replace("\\", "/")
            if len(digest) != 64 or any(char not in "0123456789ABCDEF" for char in digest):
                raise ReleaseIdentityError(f"hardware manifest has invalid SHA-256 for {role}")
            if not path.endswith(f"/{component}/{stem}.{suffix}"):
                raise ReleaseIdentityError(f"hardware manifest path does not match role {role}")
            component_expected[f"{suffix}_sha256"] = digest
        expected[component] = component_expected

    media_x4 = expected["media_x4"]
    if str(frozen.get("directory", "")).replace("\\", "/") != str(
        overlay_set["media_x4"]
    ).replace("\\", "/"):
        raise ReleaseIdentityError("frozen_overlay directory is not overlay_set.media_x4")
    for suffix in ("bit", "hwh"):
        field = f"{suffix}_sha256"
        if str(frozen.get(field, "")).upper() != media_x4[field]:
            raise ReleaseIdentityError(f"frozen_overlay.{field} disagrees with hardware manifest")
    return expected


def inspect_overlay_set(
    overlay_directories: Mapping[str, Path | str],
    *,
    application: Mapping[str, Any] | None = None,
    release: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute actual hashes and return a fail-closed deployment identity report."""

    using_loaded_manifests = application is None and release is None
    selected_application = _APPLICATION if application is None else application
    selected_release = _HARDWARE_RELEASE if release is None else release
    try:
        expected = validate_manifest_contract(selected_application, selected_release)
    except ReleaseIdentityError as error:
        return {
            "identity_match": False,
            "manifest_match": False,
            "error": str(error),
            "overlays": {},
        }

    overlays: dict[str, Any] = {}
    all_match = True
    for component, expected_entry in expected.items():
        directory_value = overlay_directories.get(component)
        directory = None if directory_value is None else Path(directory_value).resolve()
        files: dict[str, Any] = {}
        component_match = directory is not None
        for suffix in ("bit", "hwh"):
            path = (
                None
                if directory is None
                else directory / f"{expected_entry['stem']}.{suffix}"
            )
            actual = sha256_file(path) if path is not None and path.is_file() else None
            wanted = expected_entry[f"{suffix}_sha256"]
            matches = actual == wanted
            component_match = component_match and matches
            files[suffix] = {
                "path": None if path is None else str(path),
                "expected_sha256": wanted,
                "actual_sha256": actual,
                "match": matches,
            }
        overlays[component] = {
            "directory": None if directory is None else str(directory),
            "identity_match": component_match,
            **files,
        }
        all_match = all_match and component_match
    report = {
        "identity_match": all_match,
        "manifest_match": True,
        "application_version": str(selected_application.get("version", "")),
        "hardware_stage": int(selected_application["hardware_stage"]),
        "release_status": str(selected_application["release_status"]),
        "overlays": overlays,
    }
    if using_loaded_manifests:
        report["manifests"] = {
            "application": {
                "path": str(APPLICATION_MANIFEST_PATH),
                "sha256": sha256_file(APPLICATION_MANIFEST_PATH),
            },
            "hardware": {
                "path": str(HARDWARE_MANIFEST_PATH),
                "sha256": sha256_file(HARDWARE_MANIFEST_PATH),
            },
        }
        release_hash_path = APPLICATION_MANIFEST_PATH.parent.parent / ".release_sha256"
        release_hash = (
            release_hash_path.read_text(encoding="ascii").strip().upper()
            if release_hash_path.is_file()
            else None
        )
        report["release_archive_sha256"] = (
            release_hash
            if release_hash is not None
            and len(release_hash) == 64
            and all(char in "0123456789ABCDEF" for char in release_hash)
            else None
        )
    return report


def require_overlay_identity(
    overlay_directories: Mapping[str, Path | str],
    *,
    application: Mapping[str, Any] | None = None,
    release: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = inspect_overlay_set(
        overlay_directories, application=application, release=release
    )
    if not report["identity_match"]:
        mismatches = [
            name
            for name, value in report.get("overlays", {}).items()
            if not value.get("identity_match")
        ]
        detail = report.get("error") or ", ".join(mismatches) or "unknown mismatch"
        raise ReleaseIdentityError(f"deployed Overlay identity mismatch: {detail}")
    return report


def _resolve_video_pipeline(application: Mapping[str, Any]) -> str:
    features = application.get("features")
    if not isinstance(features, dict):
        raise ReleaseIdentityError("application manifest has no features object")
    for feature_name in ("full_frame_video_x4", "live_camera_x4"):
        feature = features.get(feature_name)
        if isinstance(feature, dict):
            pipeline = feature.get("pipeline")
            if isinstance(pipeline, str) and pipeline:
                return pipeline
    raise ReleaseIdentityError("application manifest has no full-frame or live-camera pipeline")


_APPLICATION, APPLICATION_MANIFEST_PATH = _load_first_manifest(
    "application.json", "SR_LUT_APPLICATION_MANIFEST", "application manifest"
)
_HARDWARE_RELEASE, HARDWARE_MANIFEST_PATH = _load_hardware_manifest()
EXPECTED_OVERLAYS = validate_manifest_contract(_APPLICATION, _HARDWARE_RELEASE)
HARDWARE_STAGE = int(_APPLICATION["hardware_stage"])
APPLICATION_VERSION = str(_APPLICATION["version"])
VIDEO_PIPELINE = _resolve_video_pipeline(_APPLICATION)
EXPECTED_BIT_SHA256 = EXPECTED_OVERLAYS["media_x4"]["bit_sha256"]
EXPECTED_HWH_SHA256 = EXPECTED_OVERLAYS["media_x4"]["hwh_sha256"]
SCALE = 4
MIN_FRAME_WIDTH = 32
MAX_FRAME_WIDTH = 480
MIN_FRAME_HEIGHT = 32
MAX_FRAME_HEIGHT = 320


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a deployed SR-LUT Overlay set")
    parser.add_argument("--application-manifest", type=Path)
    parser.add_argument("--hardware-manifest", type=Path)
    parser.add_argument("--overlay-root", type=Path, required=True)
    args = parser.parse_args(argv)
    application = (
        _APPLICATION
        if args.application_manifest is None
        else _read_json(args.application_manifest, "application manifest")
    )
    release = (
        _HARDWARE_RELEASE
        if args.hardware_manifest is None
        else _read_json(args.hardware_manifest, "hardware manifest")
    )
    directories = {name: args.overlay_root / name for name in OVERLAY_LAYOUT}
    report = inspect_overlay_set(directories, application=application, release=release)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["identity_match"] else 3


__all__ = [
    "APPLICATION_MANIFEST_PATH",
    "APPLICATION_VERSION",
    "EXPECTED_BIT_SHA256",
    "EXPECTED_HWH_SHA256",
    "EXPECTED_OVERLAYS",
    "HARDWARE_MANIFEST_PATH",
    "HARDWARE_STAGE",
    "MAX_FRAME_HEIGHT",
    "MAX_FRAME_WIDTH",
    "MIN_FRAME_HEIGHT",
    "MIN_FRAME_WIDTH",
    "OVERLAY_LAYOUT",
    "ReleaseIdentityError",
    "SCALE",
    "VIDEO_PIPELINE",
    "inspect_overlay_set",
    "require_overlay_identity",
    "sha256_file",
    "validate_manifest_contract",
]


if __name__ == "__main__":
    raise SystemExit(main())
