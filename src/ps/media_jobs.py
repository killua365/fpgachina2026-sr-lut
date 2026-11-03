"""Persistent video jobs shared by the board portal and PC receiver agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
from typing import Any, BinaryIO, Callable, Mapping, Optional

from video_pipeline.identity import APPLICATION_VERSION
from video_pipeline.identity import HARDWARE_STAGE
from video_pipeline.identity import MAX_FRAME_HEIGHT
from video_pipeline.identity import MAX_FRAME_WIDTH
from video_pipeline.identity import MIN_FRAME_HEIGHT
from video_pipeline.identity import MIN_FRAME_WIDTH
from video_pipeline.identity import SCALE
from video_pipeline.identity import VIDEO_PIPELINE


VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
ACTIVE_STATES = {"processing", "receiving"}
MAX_VIDEO_UPLOAD_BYTES = 1024 * 1024 * 1024
PORTAL_VIDEO_QUEUE_DEPTH = 4
PORTAL_NETWORK_IRQ_AFFINITY = False
# A queued job is useful only while a PC agent has a chance to claim it.  A
# finite lease prevents a dead/stopped agent from leaving the portal in an
# apparently endless "waiting" state across browser or board restarts.
PORTAL_QUEUED_JOB_TIMEOUT_SECONDS = 300.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_filename(name: str) -> str:
    clean = Path(name).name.strip().replace("\x00", "")
    return clean[:160] or "input.mp4"


def probe_video(path: Path, scale: int = SCALE) -> dict[str, Any]:
    """Reject media that cannot satisfy the frozen dynamic-frame contract."""

    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("video cannot be decoded")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        declared_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError("video contains no decodable frames")
        height, width = frame.shape[:2]
        if not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH or not MIN_FRAME_HEIGHT <= height <= MAX_FRAME_HEIGHT:
            raise ValueError(
                "video frame must be within "
                f"width {MIN_FRAME_WIDTH}..{MAX_FRAME_WIDTH}, height {MIN_FRAME_HEIGHT}..{MAX_FRAME_HEIGHT}, "
                f"got {width}x{height}"
            )
        if fps <= 0 or fps != fps:
            raise ValueError(f"video FPS is invalid: {fps}")
        if int(scale) not in (2, 4):
            raise ValueError("scale must be 2 or 4")
        return {
            "width": int(width),
            "height": int(height),
            "output_width": int(width * int(scale)),
            "output_height": int(height * int(scale)),
            "scale": int(scale),
            "fps": fps,
            "declared_frame_count": declared_frames,
        }
    finally:
        capture.release()


@dataclass
class VideoJob:
    job_id: str
    stage: int
    directory: Path
    input_path: Path
    input_name: str
    input_mime: str
    input_size_bytes: int
    input_sha256: str
    scale: int = SCALE
    application_version: str = APPLICATION_VERSION
    video_pipeline: str = VIDEO_PIPELINE
    requires_pc_agent: bool = True
    state: str = "uploaded"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    receiver: Optional[dict[str, Any]] = None
    input_probe: Optional[dict[str, Any]] = None
    board_report: Optional[dict[str, Any]] = None
    agent_report: Optional[dict[str, Any]] = None
    input_preview_path: Optional[Path] = None
    input_preview_meta: Optional[dict[str, Any]] = None
    output_path: Optional[Path] = None
    output_meta: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    cancel_requested: bool = False

    def public(self) -> dict[str, Any]:
        base = f"/api/video/jobs/{self.job_id}"
        input_meta = {
            "filename": self.input_name,
            "mime_type": self.input_mime,
            "size_bytes": self.input_size_bytes,
            "sha256": self.input_sha256,
            "probe": self.input_probe,
            "url": base + "/input",
            "preview": None,
        }
        if self.input_preview_meta is not None:
            input_meta["preview"] = {
                **self.input_preview_meta,
                "url": base + "/input-preview",
            }
        return {
            "id": self.job_id,
            "stage": self.stage,
            "hardware_stage": self.stage,
            "application_version": self.application_version,
            "video_pipeline": self.video_pipeline,
            "scale": self.scale,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "input": input_meta,
            "output": None if self.output_meta is None else {**self.output_meta, "url": base + "/output"},
            "receiver": self.receiver,
            "requires_pc_agent": self.requires_pc_agent,
            "cancel_requested": self.cancel_requested,
            "error": self.error,
            "report_url": base + "/report",
        }

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "identity": {
                "hardware_stage": self.stage,
                "application_version": self.application_version,
                "video_pipeline": self.video_pipeline,
            },
            "portal_job": self.public(),
            "board": self.board_report,
            "pc_receiver": self.agent_report,
        }


class VideoJobManager:
    """Own uploads, state transitions and serialized access to the FPGA overlay."""

    def __init__(
        self,
        processor: Any,
        storage_dir: Path,
        *,
        backend: Optional[Callable[[VideoJob, Optional[str], Optional[int]], Mapping[str, Any]]] = None,
        probe: Callable[[Path], Mapping[str, Any]] = probe_video,
        queue_depth: int = PORTAL_VIDEO_QUEUE_DEPTH,
        network_irq_affinity: bool = PORTAL_NETWORK_IRQ_AFFINITY,
        queued_job_timeout_seconds: float = PORTAL_QUEUED_JOB_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(queue_depth, bool) or not 2 <= int(queue_depth) <= 8:
            raise ValueError("queue_depth must be an integer in 2..8")
        if not isinstance(network_irq_affinity, bool):
            raise TypeError("network_irq_affinity must be a boolean")
        if isinstance(queued_job_timeout_seconds, bool) or float(queued_job_timeout_seconds) <= 0:
            raise ValueError("queued_job_timeout_seconds must be positive")
        self.stage = HARDWARE_STAGE
        self.application_version = APPLICATION_VERSION
        self.pipeline = VIDEO_PIPELINE
        self.queue_depth = int(queue_depth)
        self.network_irq_affinity = network_irq_affinity
        self.queued_job_timeout_seconds = float(queued_job_timeout_seconds)
        self.processor = processor
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # Keep jobs from older releases out of the active scan. The portal runs
        # as root on the board, so moving an old directory on the same filesystem
        # is fast and preserves the data for manual recovery if needed.
        self.legacy_storage_dir = self.storage_dir.parent / f"{self.storage_dir.name}_legacy"
        self._backend = backend
        self._probe = probe
        self._jobs: dict[str, VideoJob] = {}
        self._lock = threading.RLock()
        self._load_existing_jobs()

    @staticmethod
    def _timestamp_age_seconds(timestamp: str) -> Optional[float]:
        try:
            parsed = datetime.fromisoformat(str(timestamp))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def _expire_queued_jobs_locked(self) -> int:
        """Fail unclaimed jobs whose queue lease has elapsed."""

        expired = 0
        for job in self._jobs.values():
            if job.state != "queued":
                continue
            age = self._timestamp_age_seconds(job.updated_at or job.created_at)
            if age is None or age <= self.queued_job_timeout_seconds:
                continue
            job.state = "failed"
            job.error = (
                "PC 视频助手在队列超时窗口内未领取任务；请确认助手正在运行后重新上传"
            )
            job.cancel_requested = True
            self._persist(job)
            expired += 1
        return expired

    def _archive_invalid_job(self, directory: Path) -> bool:
        try:
            self.legacy_storage_dir.mkdir(parents=True, exist_ok=True)
            target = self.legacy_storage_dir / directory.name
            if target.exists():
                target = self.legacy_storage_dir / f"{directory.name}-{secrets.token_hex(4)}"
            shutil.move(str(directory), str(target))
            return True
        except OSError as error:
            print(
                f"Could not archive invalid persisted video job {directory.name}: {error}",
                flush=True,
            )
            return False

    def _load_existing_jobs(self) -> None:
        """Restore durable jobs and fail work interrupted by a portal restart."""

        archived = 0
        invalid = 0
        for directory in sorted(self.storage_dir.iterdir()):
            job_path = directory / "job.json"
            if not directory.is_dir() or not job_path.is_file():
                continue
            try:
                persisted = json.loads(job_path.read_text(encoding="utf-8"))
                public = persisted["portal_job"]
                input_meta = public["input"]
                input_name = str(input_meta["filename"])
                input_path = directory / ("input" + Path(input_name).suffix.lower())
                if not input_path.is_file():
                    raise FileNotFoundError(input_path)
                output_meta = public.get("output")
                output_path = None
                if output_meta is not None:
                    output_path = directory / str(output_meta["filename"])
                    if not output_path.is_file():
                        output_meta = None
                        output_path = None
                preview_meta = input_meta.get("preview")
                preview_path = None
                if preview_meta is not None:
                    preview_path = directory / "input_preview.mp4"
                    if not preview_path.is_file():
                        preview_meta = None
                        preview_path = None
                state = str(public["state"])
                error = public.get("error")
                interrupted = state in ACTIVE_STATES or (
                    self.processor.preview and state == "queued"
                )
                if interrupted:
                    state = "failed"
                    error = "portal restarted before the video job completed"
                elif state == "completed" and output_meta is None:
                    state = "failed"
                    error = "persisted video output is missing"
                elif state == "completed" and not self.processor.preview and preview_meta is None:
                    state = "failed"
                    error = "persisted browser input preview is missing"
                job = VideoJob(
                    job_id=str(public["id"]),
                    stage=int(public.get("hardware_stage", public["stage"])),
                    directory=directory,
                    input_path=input_path,
                    input_name=input_name,
                    input_mime=str(input_meta["mime_type"]),
                    input_size_bytes=int(input_meta["size_bytes"]),
                    input_sha256=str(input_meta["sha256"]),
                    scale=int(public.get("scale", SCALE)),
                    application_version=str(
                        public.get("application_version", APPLICATION_VERSION)
                    ),
                    video_pipeline=str(public.get("video_pipeline", VIDEO_PIPELINE)),
                    requires_pc_agent=bool(
                        public.get("requires_pc_agent", not self.processor.preview)
                    ),
                    state=state,
                    created_at=str(public["created_at"]),
                    updated_at=str(public["updated_at"]),
                    receiver=public.get("receiver"),
                    input_probe=input_meta.get("probe"),
                    board_report=persisted.get("board"),
                    agent_report=persisted.get("pc_receiver"),
                    input_preview_path=preview_path,
                    input_preview_meta=preview_meta,
                    output_path=output_path,
                    output_meta=output_meta,
                    error=error,
                    cancel_requested=bool(public.get("cancel_requested", False)),
                )
                if job.stage != HARDWARE_STAGE or job.application_version != APPLICATION_VERSION:
                    raise ValueError("persisted job identity is incompatible")
                self._jobs[job.job_id] = job
                if interrupted or (
                    public["state"] == "completed"
                    and (output_meta is None or (not self.processor.preview and preview_meta is None))
                ):
                    self._persist(job)
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
                invalid += 1
                if self._archive_invalid_job(directory):
                    archived += 1
        if invalid:
            print(
                "Persisted video jobs: "
                f"invalid={invalid} archived={archived} active={len(self._jobs)}",
                flush=True,
            )
        with self._lock:
            self._expire_queued_jobs_locked()

    def capabilities(self) -> dict[str, Any]:
        return {
            "hardware_stage": self.stage,
            "application_version": self.application_version,
            "video_pipeline": self.pipeline,
            "requires_pc_agent": not self.processor.preview,
            "accepted_extensions": sorted(VIDEO_SUFFIXES),
            "maximum_upload_bytes": MAX_VIDEO_UPLOAD_BYTES,
            "input_geometry": {
                "minimum_width": MIN_FRAME_WIDTH,
                "maximum_width": MAX_FRAME_WIDTH,
                "minimum_height": MIN_FRAME_HEIGHT,
                "maximum_height": MAX_FRAME_HEIGHT,
                "scale": SCALE,
                "supported_scales": [2, 4],
            },
            "scheduling": "exclusive-video-job",
            "runtime_defaults": {
                "queue_depth": self.queue_depth,
                "queued_job_timeout_seconds": self.queued_job_timeout_seconds,
                "cpu_affinity": True,
                "network_irq_affinity": self.network_irq_affinity,
                "network_irq_policy": (
                    "explicit-affinity"
                    if self.network_irq_affinity
                    else "kernel-managed"
                ),
            },
        }

    def _persist(self, job: VideoJob) -> None:
        job.updated_at = utc_now()
        (job.directory / "job.json").write_text(
            json.dumps(job.report(), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    def create(
        self, stream: BinaryIO, length: int, filename: str, mime_type: str,
        *, scale: int = SCALE,
    ) -> VideoJob:
        if length <= 0:
            raise ValueError("video upload is empty")
        if length > MAX_VIDEO_UPLOAD_BYTES:
            raise ValueError("video exceeds the 1 GiB upload limit")
        filename = _safe_filename(filename)
        if Path(filename).suffix.lower() not in VIDEO_SUFFIXES:
            raise ValueError("supported video formats are MP4, MOV, AVI, MKV, WebM, M4V, MPEG and MPG")
        if mime_type and not (mime_type.startswith("video/") or mime_type == "application/octet-stream"):
            raise ValueError("uploaded content is not identified as video")
        if int(scale) not in (2, 4):
            raise ValueError("scale must be 2 or 4")
        scale = int(scale)
        job_id = secrets.token_hex(12)
        directory = self.storage_dir / job_id
        directory.mkdir(parents=True, exist_ok=False)
        input_path = directory / ("input" + Path(filename).suffix.lower())
        digest = hashlib.sha256()
        remaining = length
        try:
            with input_path.open("wb") as output:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("video upload ended before Content-Length bytes were received")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
        except BaseException:
            input_path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        try:
            try:
                input_probe = dict(self._probe(input_path, scale))
            except TypeError:
                input_probe = dict(self._probe(input_path))
                input_probe["output_width"] = int(input_probe["width"]) * scale
                input_probe["output_height"] = int(input_probe["height"]) * scale
                input_probe["scale"] = scale
        except BaseException:
            input_path.unlink(missing_ok=True)
            directory.rmdir()
            raise
        job = VideoJob(
            job_id=job_id,
            stage=self.stage,
            directory=directory,
            input_path=input_path,
            input_name=filename,
            input_mime=mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
            input_size_bytes=length,
            input_sha256=digest.hexdigest().upper(),
            scale=scale,
            requires_pc_agent=not self.processor.preview,
            state="queued",
            input_probe=input_probe,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
        if self.processor.preview:
            self._start(job)
        return job

    def get(self, job_id: str) -> VideoJob:
        with self._lock:
            self._expire_queued_jobs_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

    def next_queued(self) -> Optional[VideoJob]:
        with self._lock:
            self._expire_queued_jobs_locked()
            if any(job.state in ACTIVE_STATES for job in self._jobs.values()):
                return None
            queued = [job for job in self._jobs.values() if job.state == "queued"]
            return min(queued, key=lambda item: item.created_at) if queued else None

    def active_jobs(self) -> list[VideoJob]:
        """Return a stable snapshot of jobs that may still own the FPGA."""

        with self._lock:
            return [
                job for job in self._jobs.values() if job.state in ACTIVE_STATES
            ]

    def cancel_all(self) -> list[VideoJob]:
        """Request cancellation for every non-terminal job.

        Queued/receiving jobs become terminal immediately.  A processing job
        remains active until its backend observes ``cancel_requested`` and
        releases the hardware session, so mode reset cannot race the FPGA.
        """

        with self._lock:
            cancelled: list[VideoJob] = []
            for job in self._jobs.values():
                if job.state in TERMINAL_STATES:
                    continue
                job.cancel_requested = True
                if job.state in {"uploaded", "queued", "receiving"}:
                    job.state = "cancelled"
                self._persist(job)
                cancelled.append(job)
            return cancelled

    def claim(self, job_id: str, receiver_host: str, receiver_port: int) -> VideoJob:
        if self.processor.preview:
            raise ValueError("software preview does not use a PC receiver agent")
        if not receiver_host or not 1 <= int(receiver_port) <= 65535:
            raise ValueError("receiver host and port are invalid")
        with self._lock:
            job = self.get(job_id)
            if job.state != "queued":
                raise RuntimeError(f"job cannot be claimed from state {job.state}")
            if any(
                other.job_id != job_id and other.state in ACTIVE_STATES
                for other in self._jobs.values()
            ):
                raise RuntimeError("another video job already owns the FPGA")
            job.receiver = {"host": receiver_host, "port": int(receiver_port), "claimed_at": utc_now()}
            job.state = "processing"
            self._persist(job)
        self._start(job, receiver_host, int(receiver_port), state_already_set=True)
        return job

    def cancel(self, job_id: str) -> VideoJob:
        with self._lock:
            job = self.get(job_id)
            if job.state in TERMINAL_STATES:
                return job
            job.cancel_requested = True
            if job.state in {"uploaded", "queued", "receiving"}:
                job.state = "cancelled"
            self._persist(job)
            return job

    def _start(
        self,
        job: VideoJob,
        receiver_host: Optional[str] = None,
        receiver_port: Optional[int] = None,
        *,
        state_already_set: bool = False,
    ) -> None:
        if not state_already_set:
            with self._lock:
                if job.state == "cancelled":
                    return
                job.state = "processing"
                self._persist(job)
        thread = threading.Thread(
            target=self._run,
            args=(job, receiver_host, receiver_port),
            name=f"video-{job.job_id}",
            daemon=True,
        )
        thread.start()

    def _run(self, job: VideoJob, receiver_host: Optional[str], receiver_port: Optional[int]) -> None:
        try:
            started = time.perf_counter()
            report = dict(self._execute(job, receiver_host, receiver_port))
            report.setdefault("portal_wall_seconds", time.perf_counter() - started)
            with self._lock:
                job.board_report = report
                if job.cancel_requested:
                    job.state = "cancelled"
                elif report.get("status") != "pass":
                    job.state = "failed"
                    job.error = str(report.get("error") or "video pipeline did not pass")
                elif self.processor.preview:
                    self._set_output(
                        job, job.directory / f"output_x{job.scale}.avi",
                        "video/x-msvideo"
                    )
                    job.state = "completed"
                elif job.agent_report is not None and job.agent_report.get("status") != "pass":
                    job.state = "failed"
                    job.error = str(job.agent_report.get("error") or "PC receiver reported failure")
                else:
                    job.state = "receiving"
                    self._maybe_complete(job)
                self._persist(job)
        except BaseException as exc:
            with self._lock:
                if job.cancel_requested:
                    job.state = "cancelled"
                    job.error = None
                else:
                    job.state = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"
                report_path = job.directory / "board_report.json"
                if report_path.is_file():
                    try:
                        job.board_report = json.loads(report_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
                self._persist(job)

    def _execute(self, job: VideoJob, receiver_host: Optional[str], receiver_port: Optional[int]) -> Mapping[str, Any]:
        if self._backend is not None:
            return self._backend(job, receiver_host, receiver_port)
        report_path = job.directory / "board_report.json"
        with self.processor.hardware_session(job.scale) as overlay:
            if self.processor.preview:
                return self._preview_video(
                    job.input_path, job.directory / f"output_x{job.scale}.avi",
                    report_path, scale=job.scale
                )
            if receiver_host is None or receiver_port is None:
                raise RuntimeError("PC receiver endpoint is missing")
            from video_pipeline.board_pipeline import process_video_yuv420

            overlay_dir_value = os.environ.get(
                f"SR_LUT_X{job.scale}_OVERLAY_DIR"
            )
            overlay_dir = (
                Path(overlay_dir_value) if overlay_dir_value else None
            )

            return process_video_yuv420(
                job.input_path,
                report_path,
                receiver_host,
                receiver_port,
                overlay=overlay,
                overlay_dir=overlay_dir,
                fpga_output="mb420" if job.scale == 4 else "rgbx",
                scale=job.scale,
                queue_depth=self.queue_depth,
                cpu_affinity=True,
                network_irq_affinity=self.network_irq_affinity,
                cancel_check=lambda: job.cancel_requested,
            )

    @staticmethod
    def _preview_video(
        input_path: Path, output_path: Path, report_path: Path, *, scale: int = SCALE
    ) -> Mapping[str, Any]:
        import cv2

        capture = cv2.VideoCapture(str(input_path))
        if not capture.isOpened():
            raise RuntimeError("input video cannot be decoded")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        ok, frame = capture.read()
        if not ok or frame is None or fps <= 0:
            capture.release()
            raise RuntimeError("video has no decodable frames or valid FPS")
        height, width = frame.shape[:2]
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            fps,
            (width * scale, height * scale),
        )
        if not writer.isOpened():
            capture.release()
            raise RuntimeError("MJPEG preview encoder could not be opened")
        count = 0
        started = time.perf_counter()
        try:
            while ok and frame is not None:
                writer.write(
                    cv2.resize(
                        frame,
                        (width * scale, height * scale),
                        interpolation=cv2.INTER_CUBIC,
                    )
                )
                count += 1
                ok, frame = capture.read()
        finally:
            capture.release()
            writer.release()
        elapsed = time.perf_counter() - started
        report = {
            "schema_version": 1,
            "status": "pass",
            "execution_mode": "software_preview",
            "formal_fpga_result": False,
            "input": {"width": width, "height": height, "fps": fps, "decoded_frame_count": count},
            "output": {"width": width * scale, "height": height * scale, "fps": fps, "encoded_frame_count": count, "codec_fourcc": "MJPG", "size_bytes": output_path.stat().st_size, "scale": scale},
            "host_performance": {"total_processing_seconds": elapsed, "total_processing_fps": count / elapsed},
            "pl_performance": None,
        }
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return report

    def accept_output(self, job_id: str, stream: BinaryIO, length: int, filename: str, mime_type: str) -> VideoJob:
        if self.processor.preview:
            raise ValueError("software preview output is produced on the board host")
        if length <= 0 or length > MAX_VIDEO_UPLOAD_BYTES:
            raise ValueError("output upload length is invalid")
        with self._lock:
            job = self.get(job_id)
            if job.state not in {"processing", "receiving"}:
                raise RuntimeError(f"output cannot be uploaded in state {job.state}")
        suffix = Path(_safe_filename(filename)).suffix.lower() or ".mp4"
        if suffix not in VIDEO_SUFFIXES:
            raise ValueError("video output has an unsupported file extension")
        if mime_type and not (
            mime_type.startswith("video/") or mime_type == "application/octet-stream"
        ):
            raise ValueError("uploaded output is not identified as video")
        output_path = job.directory / (f"output_x{job.scale}" + suffix)
        remaining = length
        with output_path.open("wb") as output:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    output_path.unlink(missing_ok=True)
                    raise ValueError("output upload ended early")
                output.write(chunk)
                remaining -= len(chunk)
        with self._lock:
            self._set_output(job, output_path, mime_type or mimetypes.guess_type(output_path.name)[0] or "application/octet-stream")
            self._maybe_complete(job)
            self._persist(job)
            return job

    def accept_input_preview(
        self,
        job_id: str,
        stream: BinaryIO,
        length: int,
        filename: str,
        mime_type: str,
    ) -> VideoJob:
        if self.processor.preview:
            raise ValueError("software preview does not accept a PC browser preview")
        if length <= 0 or length > MAX_VIDEO_UPLOAD_BYTES:
            raise ValueError("input preview upload length is invalid")
        if Path(_safe_filename(filename)).suffix.lower() != ".mp4":
            raise ValueError("input preview must use an MP4 container")
        if mime_type not in {"video/mp4", "application/octet-stream"}:
            raise ValueError("input preview must be identified as video/mp4")
        with self._lock:
            job = self.get(job_id)
            if job.state not in {"processing", "receiving"}:
                raise RuntimeError(
                    f"input preview cannot be uploaded in state {job.state}"
                )
        preview_path = job.directory / "input_preview.mp4"
        remaining = length
        digest = hashlib.sha256()
        with preview_path.open("wb") as output:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    preview_path.unlink(missing_ok=True)
                    raise ValueError("input preview upload ended early")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        with self._lock:
            job.input_preview_path = preview_path
            job.input_preview_meta = {
                "filename": preview_path.name,
                "mime_type": "video/mp4",
                "size_bytes": length,
                "sha256": digest.hexdigest().upper(),
                "validation_status": "pending",
            }
            self._persist(job)
            return job

    @staticmethod
    def _validated_preview_meta(job: VideoJob, report: Mapping[str, Any]) -> dict[str, Any]:
        if job.input_preview_path is None or job.input_preview_meta is None:
            raise ValueError("input preview file must be uploaded before the agent report")
        preview = report.get("input_preview")
        if not isinstance(preview, Mapping) or preview.get("status") != "pass":
            raise ValueError("agent report does not contain a passed input preview")
        active_width = int(preview.get("active_width", 0))
        active_height = int(preview.get("active_height", 0))
        coded_width = int(preview.get("coded_width", 0))
        coded_height = int(preview.get("coded_height", 0))
        frame_count = int(preview.get("decoded_frame_count", 0))
        expected_width = int((job.input_probe or {}).get("width", 0))
        expected_height = int((job.input_probe or {}).get("height", 0))
        expected_frames = int((job.input_probe or {}).get("declared_frame_count", 0))
        if (active_width, active_height) != (expected_width, expected_height):
            raise ValueError("input preview active geometry does not match the uploaded video")
        if (coded_width, coded_height) != (
            active_width + active_width % 2,
            active_height + active_height % 2,
        ):
            raise ValueError("input preview coded geometry does not match even padding")
        if str(preview.get("codec_name")) != "h264" or str(preview.get("pixel_format")) != "yuv420p":
            raise ValueError("input preview must be H.264 yuv420p")
        if expected_frames > 0 and frame_count != expected_frames:
            raise ValueError(
                f"input preview frame count mismatch: {frame_count}/{expected_frames}"
            )
        actual_hash = sha256_file(job.input_preview_path)
        if actual_hash != job.input_preview_meta.get("sha256") or actual_hash != str(preview.get("sha256", "")).upper():
            raise ValueError("input preview SHA-256 does not match the uploaded file")
        return {
            **job.input_preview_meta,
            "validation_status": "pass",
            "browser_compatible": True,
            "active_width": active_width,
            "active_height": active_height,
            "coded_width": coded_width,
            "coded_height": coded_height,
            "fps": float(preview.get("fps", 0.0)),
            "decoded_frame_count": frame_count,
            "duration_seconds": float(preview.get("duration_seconds", 0.0)),
            "codec_name": "h264",
            "pixel_format": "yuv420p",
            "generation_mode": str(preview.get("generation_mode", "transcode")),
        }

    def accept_agent_report(self, job_id: str, report: Mapping[str, Any]) -> VideoJob:
        with self._lock:
            job = self.get(job_id)
            if job.state not in {"processing", "receiving"}:
                raise RuntimeError(f"agent report cannot be uploaded in state {job.state}")
            job.agent_report = dict(report)
            if job.agent_report.get("status") != "pass":
                job.state = "failed"
                job.error = str(job.agent_report.get("error") or "PC receiver reported failure")
            else:
                job.input_preview_meta = self._validated_preview_meta(
                    job,
                    job.agent_report,
                )
                self._maybe_complete(job)
            self._persist(job)
            return job

    def _set_output(self, job: VideoJob, output_path: Path, mime_type: str) -> None:
        if not output_path.is_file():
            raise RuntimeError("video pipeline did not create its output file")
        job.output_path = output_path
        job.output_meta = {
            "filename": output_path.name,
            "mime_type": mime_type,
            "size_bytes": output_path.stat().st_size,
            "sha256": sha256_file(output_path),
        }

    @staticmethod
    def _maybe_complete(job: VideoJob) -> None:
        if job.cancel_requested:
            job.state = "cancelled"
        elif (
            job.board_report is not None
            and job.agent_report is not None
            and job.output_meta is not None
            and job.input_preview_meta is not None
            and job.input_preview_meta.get("validation_status") == "pass"
        ):
            board_ok = job.board_report.get("status") == "pass"
            agent_ok = job.agent_report.get("status") == "pass"
            if board_ok and agent_ok:
                job.state = "completed"
            else:
                job.state = "failed"
                job.error = "board or PC receiver report did not pass"


__all__ = [
    "ACTIVE_STATES",
    "APPLICATION_VERSION",
    "HARDWARE_STAGE",
    "MAX_VIDEO_UPLOAD_BYTES",
    "PORTAL_NETWORK_IRQ_AFFINITY",
    "PORTAL_QUEUED_JOB_TIMEOUT_SECONDS",
    "PORTAL_VIDEO_QUEUE_DEPTH",
    "TERMINAL_STATES",
    "VIDEO_PIPELINE",
    "VIDEO_SUFFIXES",
    "VideoJob",
    "VideoJobManager",
    "probe_video",
]
