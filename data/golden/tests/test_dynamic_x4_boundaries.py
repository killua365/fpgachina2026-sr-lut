import json
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class DynamicX4BoundaryTests(unittest.TestCase):
    def test_manifest_covers_minimum_odd_and_maximum(self):
        path = PROJECT_ROOT / "data/golden" / "dynamic_x4_boundary_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        shapes = {
            tuple(item["input_shape_hwc"][:2]) for item in manifest["images"]
        }
        self.assertEqual(shapes, {(32, 32), (257, 319), (320, 320)})

    def test_frozen_boundary_assets(self):
        script = (
            PROJECT_ROOT
            / "data/golden"
            / "tools"
            / "generate_dynamic_boundary_vectors.py"
        )
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "--verify-only"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("DYNAMIC_X4_BOUNDARY_VECTORS_PASS cases=3", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
