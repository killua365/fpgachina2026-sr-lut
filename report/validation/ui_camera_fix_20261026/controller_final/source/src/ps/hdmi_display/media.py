"""Offline local video import into bounded-memory, random-access RGB frames."""

from __future__ import annotations

from fractions import Fraction
import hashlib
from .artifact_access import prepare_readable_artifact
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable

import numpy as np

from .process import ImportCancelled, run_cancellable, run_cancellable_steps


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            if cancel is not None and cancel.is_set():
                raise ImportCancelled("Operation cancelled while hashing")
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path, ffprobe: str = "ffprobe", cancel: threading.Event | None = None) -> dict:
    result = run_cancellable([ffprobe, "-v", "error", "-select_streams", "v:0",
                             "-show_streams", "-show_format", "-of", "json", str(path)],
                             timeout=30, cancel=cancel)
    return validate_probe(json.loads(result.stdout))


def validate_probe(document):
    streams = document.get("streams", [])
    if len(streams) != 1:
        raise ValueError("A decodable video stream is required")
    stream = streams[0]
    if stream.get("codec_name") not in ("h264", "mjpeg"):
        raise ValueError("Supported import codecs are H.264 and MJPEG")
    if stream.get("codec_name") == "h264" and stream.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
        raise ValueError("H.264 import requires 8-bit 4:2:0")
    width, height = int(stream["width"]), int(stream["height"])
    if min(width, height) < 1 or max(width, height) > 4096:
        raise ValueError("Video input dimensions must be within 1..4096")
    rate = Fraction(stream.get("avg_frame_rate", "0/1"))
    duration = float(stream.get("duration", document.get("format", {}).get("duration", 0)))
    if rate <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("A finite video duration and frame rate are required")
    start = float(stream.get("start_time", 0))
    if not math.isfinite(start):
        raise ValueError("Invalid video start timestamp")
    return {"width": width, "height": height, "fps": str(rate), "duration": duration,
            "source_start_time": start, "codec": stream["codec_name"]}


def import_video_steps(source: Path, destination: Path, *, width: int = None, height: int = None,
                 profile=None,
                 ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 cancel: threading.Event | None = None):
    """Keep the original; atomically publish a complete imported clip directory.

    VFR input is normalized to a declared CFR timeline. Source time maps to the
    sampled original timeline, not to an asserted original decoded-frame index.
    """
    from .profiles import get_profile
    selected = get_profile(profile)
    width = selected.input_size[0] if width is None else width
    height = selected.input_size[1] if height is None else height
    if profile is not None and (width, height) != selected.input_size:
        raise ValueError('Import geometry differs from the business profile')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not 32 <= width <= 480 or not 32 <= height <= 320:
        raise ValueError("Import geometry exceeds the SR input contract")
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Choose a new clip directory inside an existing media directory")
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise ImportCancelled("Import cancelled")
    original_stat = source.stat()
    probe_job = run_cancellable_steps([ffprobe, '-v', 'error', '-select_streams', 'v:0',
        '-show_streams', '-show_format', '-of', 'json', str(source)], timeout=30, cancel=cancel)
    try:
        while True:
            try:
                next(probe_job)
            except StopIteration as finished:
                probe = validate_probe(json.loads(finished.value.stdout))
                break
            yield ('probing', 0, 0)
    finally:
        probe_job.close()
    rate = min(Fraction(probe["fps"]), Fraction(30))
    estimate = math.ceil(probe["duration"] * float(rate) + 2) * width * height * 3
    if shutil.disk_usage(destination.parent).free < estimate + 128 * 1024 * 1024:
        raise OSError("Insufficient space for the imported RGB clip")
    temp = Path(tempfile.mkdtemp(prefix=".sr_import_", dir=destination.parent))
    process = None
    try:
        raw = temp / "frames.rgb"
        scale = min(Fraction(width, probe["width"]), Fraction(height, probe["height"]))
        active_width = max(1, math.floor(probe["width"] * scale))
        active_height = max(1, math.floor(probe["height"] * scale))
        left, top = (width - active_width) // 2, (height - active_height) // 2
        filters = (f"setpts=PTS-STARTPTS,fps={rate.numerator}/{rate.denominator},"
                   f"scale={active_width}:{active_height}:flags=area,format=rgb24,"
                   f"pad={width}:{height}:{left}:{top}:color=black,setsar=1")
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                                        "-threads", "2", "-i", str(source), "-map", "0:v:0",
                                        "-an", "-sn", "-dn", "-filter_threads", "1", "-vf", filters,
                                        "-pix_fmt", "rgb24", "-f", "rawvideo", "-n", str(raw)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
            deadline = time.monotonic() + max(120, probe["duration"] * 100)
            while process.poll() is None:
                if cancel.is_set():
                    raise ImportCancelled("Import cancelled")
                if time.monotonic() > deadline:
                    raise TimeoutError("Video import exceeded its time limit")
                done = raw.stat().st_size if raw.exists() else 0
                yield ('decoding', done, estimate)
            if process.returncode:
                errors.seek(max(0, errors.tell() - 8192))
                raise RuntimeError(errors.read().decode("utf-8", "replace") or
                                   f'Video decoder exited with code {process.returncode}')
        if cancel.is_set():
            raise ImportCancelled("Import cancelled")
        frame_bytes = width * height * 3
        if not raw.exists() or not raw.stat().st_size or raw.stat().st_size % frame_bytes:
            raise RuntimeError("Decoder produced an empty or partial RGB frame")
        after = source.stat()
        if (after.st_size, after.st_mtime_ns) != (original_stat.st_size, original_stat.st_mtime_ns):
            raise RuntimeError("The source file changed during import")
        raw_hash = yield from hash_file_steps(raw, cancel)
        source_hash = yield from hash_file_steps(source, cancel)
        manifest = {
            "schema_version": 1, "kind": "hdmi_rgb_clip", "pixel_format": "rgb24",
            "width": width, "height": height, "frame_count": raw.stat().st_size // frame_bytes,
            "fps_numerator": rate.numerator, "fps_denominator": rate.denominator,
            "active_rect": {"x": left, "y": top, "width": active_width, "height": active_height},
            "frames_sha256": raw_hash, "source_name": source.name,
            "source_sha256": source_hash, "source_probe": probe,
            "time_mapping": "source_start_time + normalized_frame_index / normalized_fps",
            "normalization": "CFR timeline sampling; not original decoded frame indices",
        }
        if cancel.is_set():
            raise ImportCancelled("Import cancelled")
        final_stat = source.stat()
        if (final_stat.st_size, final_stat.st_mtime_ns) != (original_stat.st_size, original_stat.st_mtime_ns):
            raise RuntimeError("The source file changed while hashing")
        (temp / "clip.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # The caller must exclusively own the media import destination name.
        if destination.exists():
            raise FileExistsError(destination)
        prepare_readable_artifact(temp)
        temp.rename(destination)
        return destination / "clip.json"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if temp.exists():
            shutil.rmtree(temp)


def hash_file_steps(path, cancel):
    digest, done, size = hashlib.sha256(), 0, path.stat().st_size
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            if cancel.is_set():
                raise ImportCancelled('Import cancelled while hashing')
            digest.update(chunk)
            done += len(chunk)
            yield ('verifying', done, size)
    return digest.hexdigest()


def import_video(source, destination, *, progress=None, **kwargs):
    """Compatibility wrapper for noninteractive callers."""
    steps = import_video_steps(source, destination, **kwargs)
    try:
        while True:
            try:
                phase, done, total = next(steps)
            except StopIteration as finished:
                if progress: progress(1.0)
                return finished.value
            if progress and phase == 'decoding': progress(min(.99, done/max(1, total)))
            if phase in ('probing', 'decoding'): time.sleep(.05)
    finally:
        steps.close()


class RawClip:
    def __init__(self, manifest_path: Path, *, verify_hash: bool = True) -> None:
        path = Path(manifest_path)
        self.manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest = self.manifest
        if manifest.get("kind") != "hdmi_rgb_clip" or manifest.get("schema_version") != 1:
            raise ValueError("Unsupported imported clip")
        self.width, self.height, self.frame_count = (int(manifest[k]) for k in ("width", "height", "frame_count"))
        if not 32 <= self.width <= 480 or not 32 <= self.height <= 320 or self.frame_count < 1:
            raise ValueError("Invalid imported clip geometry")
        if manifest.get("pixel_format") != "rgb24":
            raise ValueError("Unsupported clip pixel format")
        self.fps = Fraction(int(manifest["fps_numerator"]), int(manifest["fps_denominator"]))
        if not 0 < self.fps <= 30:
            raise ValueError("Invalid normalized frame rate")
        self.frame_bytes = self.width * self.height * 3
        raw = path.parent / "frames.rgb"
        if raw.stat().st_size != self.frame_bytes * self.frame_count:
            raise ValueError("Imported RGB file length mismatch")
        if verify_hash and sha256_file(raw) != manifest["frames_sha256"]:
            raise ValueError("Imported RGB file hash mismatch")
        self._stream = raw.open("rb")

    def frame(self, index: int) -> np.ndarray:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.frame_count:
            raise IndexError(index)
        self._stream.seek(index * self.frame_bytes)
        data = self._stream.read(self.frame_bytes)
        if len(data) != self.frame_bytes:
            raise OSError("Short RGB frame read")
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "RawClip":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
