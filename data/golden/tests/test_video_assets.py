import hashlib
import json
from pathlib import Path
import unittest

import cv2


class RealtimeVideoAssetTests(unittest.TestCase):
    def test_320x180_gate_asset_identity_and_geometry(self):
        root = (
            Path(__file__).resolve().parents[1]
            / "source_videos"
            / "realtime_320x180"
        )
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        video_path = root / manifest["derived"]
        self.assertEqual(
            hashlib.sha256(video_path.read_bytes()).hexdigest().upper(),
            manifest["sha256"],
        )
        capture = cv2.VideoCapture(str(video_path))
        try:
            self.assertTrue(capture.isOpened())
            self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), 320)
            self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)), 180)
            self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 180)
            self.assertAlmostEqual(
                capture.get(cv2.CAP_PROP_FPS),
                30000 / 1001,
                places=3,
            )
        finally:
            capture.release()


if __name__ == "__main__":
    unittest.main(verbosity=2)
