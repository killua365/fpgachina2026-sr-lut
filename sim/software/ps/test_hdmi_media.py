import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from hdmi_display.media import ImportCancelled, RawClip, import_video, sha256_file


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required")
class LocalImportTests(unittest.TestCase):
    def test_real_h264_import_random_seek_and_tamper(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=80x64:rate=30", "-frames:v", "30",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, timeout=30)
            source_hash = sha256_file(source)
            updates = []
            manifest = import_video(source, root / "imported", progress=updates.append)
            self.assertEqual(sha256_file(source), source_hash)
            with RawClip(manifest) as clip:
                self.assertEqual(clip.frame_count, 30)
                self.assertEqual(clip.frame(29).shape, (180, 320, 3))
                self.assertNotEqual(clip.frame(0).tobytes(), clip.frame(29).tobytes())
                self.assertEqual(clip.frame(0).tobytes(), clip.frame(0).tobytes())
                with self.assertRaises(IndexError):
                    clip.frame(30)
            self.assertEqual(updates[-1], 1.0)
            data = json.loads(manifest.read_text())
            self.assertEqual(data["active_rect"], {"x": 47, "y": 0, "width": 225, "height": 180})
            with (manifest.parent / "frames.rgb").open("r+b") as stream:
                stream.write(b"bad")
            with self.assertRaisesRegex(ValueError, "hash"):
                RawClip(manifest)

    def test_precancel_never_starts_decoder_or_creates_output(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(ImportCancelled):
                import_video(root / "missing.mp4", root / "result", cancel=cancelled)
            self.assertEqual(list(root.iterdir()), [])

    def test_mjpeg_import_leaves_no_partial_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.avi"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=64x64:rate=10", "-frames:v", "10",
                            "-c:v", "mjpeg", str(source)], check=True, timeout=30)
            manifest = import_video(source, root / "imported")
            with RawClip(manifest) as clip:
                self.assertEqual(clip.frame_count, 10)
                self.assertEqual(clip.fps, 10)
            self.assertFalse(any(p.name.startswith(".sr_import_") for p in root.iterdir()))


if __name__ == "__main__":
    unittest.main()
