import hashlib
import os
import stat
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

import numpy as np

from hdmi_display.export import export_video
from hdmi_display.media import ImportCancelled, RawClip, import_video


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required")
class ExportTests(unittest.TestCase):
    def create_clip(self, root):
        source = root / "source.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=64x32:rate=30", "-frames:v", "30",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, timeout=30)
        return RawClip(import_video(source, root / "clip", width=64, height=32))

    def test_all_frames_have_raw_hashes_and_separate_lossy_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.create_clip(root) as clip:
                for scale in (2, 4):
                    inputs, outputs = [], []

                    def process(source):
                        inputs.append(hashlib.sha256(source.tobytes()).hexdigest())
                        output = np.repeat(np.repeat(source, scale, 0), scale, 1)
                        outputs.append(hashlib.sha256(output.tobytes()).hexdigest())
                        return output

                    report_path = export_video(clip, root / f"export{scale}", scale=scale,
                                               process_rgb=process, processing_identity={"backend": "host_test"})
                    report = json.loads(report_path.read_text())
                    if os.name == 'posix':
                        for folder in (root/'clip', report_path.parent):
                            self.assertEqual(stat.S_IMODE(folder.stat().st_mode) & 0o055, 0o055)
                            for path in folder.iterdir():
                                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o044, 0o044)
                    frames = [json.loads(line) for line in (report_path.parent / "frames.jsonl").read_text().splitlines()]
                    self.assertEqual([f["index"] for f in frames], list(range(30)))
                    self.assertEqual([f["input_rgb_sha256"] for f in frames], inputs)
                    self.assertEqual([f["output_rgb_sha256"] for f in frames], outputs)
                    self.assertEqual(report["encoded_stream"]["nb_read_frames"], "30")
                    self.assertFalse(report["golden_reference_verified"])
                    self.assertFalse((report_path.parent / "output.rgb").exists())
                    self.assertEqual(inputs, [hashlib.sha256(clip.frame(i).tobytes()).hexdigest() for i in range(30)])

    def test_cancel_and_bad_geometry_remove_partial_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.create_clip(root) as clip:
                cancelled = threading.Event()

                def process(source):
                    cancelled.set()
                    return source

                with self.assertRaises(ImportCancelled):
                    export_video(clip, root / "cancelled", scale=4, process_rgb=process,
                                 processing_identity={"backend": "host_test"}, cancel=cancelled)
                with self.assertRaisesRegex(ValueError, "geometry"):
                    export_video(clip, root / "invalid", scale=4, process_rgb=lambda a: a,
                                 processing_identity={"backend": "host_test"})
            self.assertFalse((root / "cancelled").exists())
            self.assertFalse((root / "invalid").exists())
            self.assertFalse(any(p.name.startswith(".sr_export_") for p in root.iterdir()))

    def test_validation_export_retains_the_actual_preencode_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.create_clip(root) as clip:
                expected=b''.join(np.repeat(np.repeat(clip.frame(i),2,0),2,1).tobytes() for i in range(clip.frame_count))
                report_path=export_video(clip,root/'retained',scale=2,
                    process_rgb=lambda a:np.repeat(np.repeat(a,2,0),2,1),
                    processing_identity={'backend':'host_test'},retain_raw_output=True)
                report=json.loads(report_path.read_text())
                self.assertTrue(report['raw_output_retained'])
                self.assertEqual((report_path.parent/'output.rgb').read_bytes(),expected)
                self.assertEqual(report['processed_rgb_sha256'],hashlib.sha256(expected).hexdigest())


if __name__ == "__main__":
    unittest.main()
