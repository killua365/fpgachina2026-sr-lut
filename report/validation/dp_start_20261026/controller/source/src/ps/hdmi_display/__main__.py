"""Local media preparation and host diagnostics; never downloads an Overlay."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .media import RawClip, import_video, sha256_file
from .patterns import PATTERNS, draw_pattern
from .scanout import MODE_720P60, MODE_1080P30, MODE_1080P60


def main():
    parser = argparse.ArgumentParser(description="SR-LUT HDMI local preparation tools")
    commands = parser.add_subparsers(dest="command", required=True)
    patterns = commands.add_parser("render-patterns")
    patterns.add_argument("destination", type=Path)
    patterns.add_argument("--mode", choices=(MODE_720P60.name, MODE_1080P30.name, MODE_1080P60.name),
                          default=MODE_720P60.name,
                          help="diagnostic raster; 1080p30 remains an opt-in hardware profile")
    importer = commands.add_parser("import-video")
    importer.add_argument("source", type=Path)
    importer.add_argument("destination", type=Path)
    inspect = commands.add_parser("inspect-clip")
    inspect.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "render-patterns":
        args.destination.mkdir(parents=True, exist_ok=False)
        mode = {MODE_720P60.name: MODE_720P60, MODE_1080P30.name: MODE_1080P30,
                MODE_1080P60.name: MODE_1080P60}[args.mode]
        buffer = np.empty((mode.height, mode.width, 4), dtype=np.uint8)
        files = []
        for pattern in PATTERNS:
            draw_pattern(buffer, pattern, sequence=37, mode=mode)
            path = args.destination / f"{pattern}.png"
            Image.fromarray(buffer[:, :, :3]).save(path)
            files.append({"path": path.name, "sha256": sha256_file(path)})
        report = {"kind": "hdmi_host_test_patterns", "mode": mode.name,
                  "width": mode.width, "height": mode.height,
                  "physical_hdmi_verified": False, "files": files}
        (args.destination / "patterns.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    elif args.command == "import-video":
        print(import_video(args.source, args.destination))
    else:
        with RawClip(args.manifest) as clip:
            print(json.dumps(clip.manifest, indent=2))


if __name__ == "__main__":
    main()
