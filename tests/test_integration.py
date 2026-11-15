from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tomlcut.cli import main

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required")
class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.config_path = self.root / "clips.toml"
        self.output_dir = self.root / "out"

        self.run_ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=15:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(self.source),
        )

        self.config_path.write_text(
            """
output_dir = "out"

[render]
preset = "ultrafast"
crf = 30

[[clips]]
source = "source.mp4"
start = "00:00:00.5"
duration = 1.0
name = "first.mp4"

[[clips]]
source = "source.mp4"
start = 2
end = 3
name = "second.mp4"

[[clips]]
source = "source.mp4"
start = "00:00:00.5"
duration = 1.0
name = "first.mp4"
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_ffmpeg(self, *arguments: str) -> None:
        assert FFMPEG is not None
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def probe_duration(self, path: Path) -> float:
        assert FFPROBE is not None
        result = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_render_and_dedupe(self) -> None:
        code, stdout, stderr = self.invoke("render", str(self.config_path))
        self.assertEqual(code, 0, stderr)
        self.assertIn("2 to render, 1 to skip", stdout)
        self.assertIn("2 rendered, 1 skipped, 0 failed", stdout)

        first = self.output_dir / "first.mp4"
        second = self.output_dir / "second.mp4"
        manifest = self.output_dir / ".tomlcut-manifest.json"
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        self.assertTrue(manifest.is_file())
        self.assertAlmostEqual(self.probe_duration(first), 1.0, delta=0.2)
        self.assertAlmostEqual(self.probe_duration(second), 1.0, delta=0.2)

        first_mtime = first.stat().st_mtime_ns
        second_mtime = second.stat().st_mtime_ns

        code, stdout, stderr = self.invoke("render", str(self.config_path))
        self.assertEqual(code, 0, stderr)
        self.assertIn("0 to render, 3 to skip", stdout)
        self.assertIn("0 rendered, 3 skipped, 0 failed", stdout)
        self.assertEqual(first.stat().st_mtime_ns, first_mtime)
        self.assertEqual(second.stat().st_mtime_ns, second_mtime)

        time.sleep(0.02)
        code, stdout, stderr = self.invoke(
            "render", "--no-dedupe", str(self.config_path)
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("3 rendered, 0 skipped", stdout)
        self.assertNotEqual(first.stat().st_mtime_ns, first_mtime)

    def test_dry_run_does_not_render(self) -> None:
        code, stdout, stderr = self.invoke("render", "--dry-run", str(self.config_path))
        self.assertEqual(code, 0, stderr)
        self.assertIn("RENDER", stdout)
        self.assertIn("dry run", stdout.lower())
        self.assertFalse(self.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
