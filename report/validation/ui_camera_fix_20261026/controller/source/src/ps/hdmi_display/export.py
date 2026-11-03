"""Bounded-memory, all-frame export independent of real-time playback."""

from __future__ import annotations

import hashlib
from .artifact_access import prepare_readable_artifact
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable

import numpy as np

from .frames import FramePair, FrameToken
from .media import ImportCancelled, RawClip


def export_video_steps(clip: RawClip, destination: Path, *, scale: int,
                 process_rgb: Callable[[np.ndarray], np.ndarray], processing_identity: dict,
                 ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 cancel: threading.Event | None = None, retain_raw_output: bool = False):
    """Export into a new directory containing MP4, report and raw frame hashes.

    Advance on the runtime's owning thread, at most once per UI tick. Each
    processing step handles one frame; encoder/probe/hash steps also yield.
    Closing this generator cancels the owned encoder and removes partial files.
    The runtime must pause playback and exclusively own FPGA/DMA for this call.
    process_rgb must enforce its own hardware timeout and return unscaled RGB.
    Disk staging avoids an encoder pipe blocking DMA cancellation indefinitely.
    This report proves sequence accounting, not golden-reference equivalence.
    """
    if isinstance(scale, bool) or scale not in (2, 4):
        raise ValueError("Scale must be 2 or 4")
    if type(retain_raw_output) is not bool:
        raise ValueError('Raw output retention must be an explicit boolean')
    # Detach mutable caller metadata and require a declared execution backend.
    identity = json.loads(json.dumps(processing_identity, allow_nan=False))
    if identity.get("backend") not in ("fpga", "host_test"):
        raise ValueError("Export must identify the processing backend")
    if identity["backend"] == "fpga":
        for key in ("bit_sha256", "hwh_sha256", "package_sha256"):
            value = identity.get(key, "")
            if len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
                raise ValueError("FPGA export requires hardware and package SHA-256 identities")
    destination = Path(destination).resolve()
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Choose a new export directory in an existing directory")
    cancel = cancel or threading.Event()

    def check_cancel():
        if cancel.is_set():
            raise ImportCancelled("Export cancelled")

    check_cancel()
    width, height = clip.width * scale, clip.height * scale
    raw_bytes = clip.frame_count * width * height * 3
    if shutil.disk_usage(destination.parent).free < raw_bytes * 2 + 128 * 1024 * 1024:
        raise OSError("Insufficient space for raw results and encoded output")
    temp = Path(tempfile.mkdtemp(prefix=".sr_export_", dir=destination.parent))
    encoder = None
    try:
        raw, movie, frame_log = temp / "output.rgb", temp / "result.mp4", temp / "frames.jsonl"
        stream_digest = hashlib.sha256()
        yield ("processing", 0, clip.frame_count)
        with raw.open("xb") as stream, frame_log.open("x", encoding="utf-8") as hashes:
            for index in range(clip.frame_count):
                check_cancel()
                source = clip.frame(index)
                input_hash = hashlib.sha256(source.tobytes()).hexdigest()
                output = process_rgb(source)
                check_cancel()
                FramePair(FrameToken(0, index), source, output, scale, index / float(clip.fps))
                packed = np.ascontiguousarray(output).tobytes()
                stream.write(packed)
                stream_digest.update(packed)
                hashes.write(json.dumps({"index": index, "time": index / float(clip.fps),
                                         "input_rgb_sha256": input_hash,
                                         "output_rgb_sha256": hashlib.sha256(packed).hexdigest()}) + "\n")
                yield ("processing", index + 1, clip.frame_count)
        check_cancel()
        with tempfile.TemporaryFile() as errors:
            encoder = subprocess.Popen([
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
                "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}",
                "-framerate", str(clip.fps), "-i", str(raw), "-an", "-c:v", "libx264",
                "-threads", "2", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-n", str(movie)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
            deadline = time.monotonic() + max(120, clip.frame_count / float(clip.fps) * 100)
            while encoder.poll() is None:
                check_cancel()
                if time.monotonic() > deadline:
                    raise TimeoutError("Video encoder exceeded its time limit")
                yield ("encoding", 0, clip.frame_count)
            if encoder.returncode:
                errors.seek(max(0, errors.tell() - 8192))
                raise RuntimeError(errors.read().decode("utf-8", "replace"))
        check_cancel()
        verification = yield from probe_steps([
            ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames,width,height,codec_name,pix_fmt",
            "-of", "json", str(movie)], cancel=cancel,
            timeout=max(30, clip.frame_count / float(clip.fps) * 10), total=clip.frame_count)
        check_cancel()
        info = json.loads(verification.stdout)["streams"][0]
        if (int(info["nb_read_frames"]), info["width"], info["height"], info["codec_name"], info["pix_fmt"]) != (
                clip.frame_count, width, height, "h264", "yuv420p"):
            raise RuntimeError("Encoded video frame count or format mismatch")
        movie_hash = yield from hash_steps(movie, cancel)
        frame_hash = yield from hash_steps(frame_log, cancel)
        report = {
            "schema_version": 1, "kind": "hdmi_video_export", "processing_identity": identity,
            "input_clip": clip.manifest, "scale": scale, "frame_count": clip.frame_count,
            "processed_rgb_sha256": stream_digest.hexdigest(), "output_width": width,
            "output_height": height, "result_mp4_sha256": movie_hash,
            "frame_log_sha256": frame_hash, "encoded_stream": info,
            "golden_reference_verified": False,
            "raw_output_retained": retain_raw_output, "encoding": "H.264 CRF18 yuv420p; lossy",
        }
        (temp / "export.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not retain_raw_output:
            raw.unlink()
        check_cancel()
        if destination.exists():
            raise FileExistsError(destination)
        prepare_readable_artifact(temp)
        temp.rename(destination)
        return destination / "export.json"
    finally:
        if encoder is not None and encoder.poll() is None:
            encoder.terminate()
            try:
                encoder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                encoder.kill()
                encoder.wait(timeout=5)
        if temp.exists():
            shutil.rmtree(temp)


def hash_steps(path, cancel):
    digest, done, size = hashlib.sha256(), 0, path.stat().st_size
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if cancel.is_set():
                raise ImportCancelled('Export cancelled while hashing')
            digest.update(chunk)
            done += len(chunk)
            yield ('finalizing', done, size)
    return digest.hexdigest()


def probe_steps(args, *, cancel, timeout, total):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors)
        try:
            deadline = time.monotonic()+timeout
            while process.poll() is None:
                if cancel.is_set():
                    raise ImportCancelled('Export cancelled during verification')
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(args, timeout)
                yield ('verifying', 0, total)
            if cancel.is_set():
                raise ImportCancelled('Export cancelled during verification')
            output.seek(0)
            errors.seek(max(0, errors.tell()-8192))
            stdout, stderr = output.read(1024*1024+1), errors.read()
            if len(stdout) > 1024*1024:
                raise RuntimeError('Encoder metadata exceeds 1 MB')
            result = subprocess.CompletedProcess(args, process.returncode,
                stdout.decode('utf-8', 'replace'), stderr.decode('utf-8', 'replace'))
            result.check_returncode()
            return result
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def export_video(clip, destination, *, progress=None, **kwargs):
    """Synchronous compatibility entry; interactive callers use the iterator."""
    steps = export_video_steps(clip, destination, **kwargs)
    try:
        while True:
            try:
                stage = next(steps)
            except StopIteration as finished:
                if progress:
                    progress('complete', clip.frame_count, clip.frame_count)
                return finished.value
            if progress:
                progress(*stage)
            if stage[0] in ('encoding', 'verifying'):
                time.sleep(0.05)
    finally:
        steps.close()
