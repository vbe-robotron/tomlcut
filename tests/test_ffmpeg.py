from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from tomlcut.config import Clip, Config, RenderOptions
from tomlcut.ffmpeg import (
    FFmpegError,
    build_command,
    check_ffmpeg,
    render_clip,
)


class BuildCommandTests(unittest.TestCase):
    def make_clip(self, suffix: str = ".mp4") -> Clip:
        return Clip(
            index=0,
            source=Path("/videos/source.mkv"),
            start=1.5,
            end=4.0,
            name=f"clip{suffix}",
            extra_args=("-metadata", "comment=clip"),
        )

    def test_encode_mp4(self) -> None:
        output = Path("/tmp/clip.mp4")
        command = build_command(self.make_clip(), RenderOptions(), output)

        self.assertEqual(command[-1], str(output))
        self.assertIn("-ss", command)
        self.assertEqual(command[command.index("-ss") + 1], "1.5")
        self.assertEqual(command[command.index("-t") + 1], "2.5")
        self.assertIn("libx264", command)
        self.assertIn("aac", command)
        self.assertIn("+faststart", command)
        self.assertIn("-progress", command)
        self.assertNotIn("copy", command)

    def test_copy_mode(self) -> None:
        output = Path("/tmp/clip.mkv")
        command = build_command(
            self.make_clip(".mkv"), RenderOptions(mode="copy"), output
        )
        self.assertIn("-c", command)
        self.assertEqual(command[command.index("-c") + 1], "copy")
        self.assertNotIn("libx264", command)
        self.assertNotIn("+faststart", command)

    def test_webm_codecs(self) -> None:
        output = Path("/tmp/clip.webm")
        command = build_command(self.make_clip(".webm"), RenderOptions(), output)
        self.assertIn("libvpx-vp9", command)
        self.assertIn("libopus", command)
        self.assertNotIn("+faststart", command)

    def test_extra_args_are_appended_before_output(self) -> None:
        output = Path("/tmp/clip.mp4")
        render = RenderOptions(extra_args=("--render-arg",))
        command = build_command(self.make_clip(), render, output)
        self.assertLess(command.index("--render-arg"), command.index("comment=clip"))
        self.assertEqual(command[-2:], ["comment=clip", str(output)])

    def test_crf_and_preset_are_used(self) -> None:
        render = RenderOptions(crf=27, preset="slow")
        command = build_command(self.make_clip(), render, Path("/tmp/clip.mp4"))
        self.assertEqual(command[command.index("-crf") + 1], "27")
        self.assertEqual(command[command.index("-preset") + 1], "slow")


class CheckFFmpegTests(unittest.TestCase):
    def test_missing_binary(self) -> None:
        with self.assertRaisesRegex(FFmpegError, "not found"):
            check_ffmpeg("tomlcut-ffmpeg-that-does-not-exist")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_real_ffmpeg(self) -> None:
        self.assertIn("ffmpeg version", check_ffmpeg("ffmpeg"))


@unittest.skipUnless(os.name == "posix", "fake executable test needs POSIX")
class RenderClipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")
        self.output_dir = self.root / "out"
        self.output_dir.mkdir()
        self.target = self.output_dir / "clip.mp4"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_config(self, ffmpeg: Path) -> Config:
        clip = Clip(
            index=0,
            source=self.source,
            start=1.0,
            end=2.0,
            name="clip.mp4",
        )
        return Config(
            path=self.root / "clips.toml",
            output_dir=self.output_dir,
            dedupe=True,
            render=RenderOptions(ffmpeg=str(ffmpeg)),
            clips=(clip,),
        )

    def write_script(self, body: str, name: str = "fake-ffmpeg") -> Path:
        path = self.root / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_successful_render_moves_output_into_place(self) -> None:
        script = self.write_script(
            """
for last; do :; done
printf 'out_time_ms=500000\\nprogress=continue\\n'
printf 'out_time_ms=1000000\\nprogress=end\\n'
printf 'fake video' > "$last"
"""
        )
        progress: list[float] = []
        command = render_clip(
            self.make_config(script),
            self.make_config(script).clips[0],
            self.target,
            on_progress=progress.append,
        )

        self.assertEqual(self.target.read_bytes(), b"fake video")
        self.assertTrue(progress)
        self.assertEqual(progress[0], 0.0)
        self.assertEqual(progress[-1], 1.0)
        self.assertEqual(command[-1], str(self.target))
        self.assertFalse(list(self.output_dir.glob(".clip.mp4.*")))

    def test_failure_cleans_up_temporary_file(self) -> None:
        script = self.write_script("echo 'boom' >&2\nexit 3\n")
        config = self.make_config(script)

        with self.assertRaisesRegex(FFmpegError, "status 3"):
            render_clip(config, config.clips[0], self.target)

        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.output_dir.glob(".clip.mp4.*")), [])

    def test_missing_output_is_an_error(self) -> None:
        script = self.write_script("exit 0\n")
        config = self.make_config(script)

        with self.assertRaisesRegex(FFmpegError, "produced no output"):
            render_clip(config, config.clips[0], self.target)

        self.assertFalse(self.target.exists())

    def test_check_ffmpeg_with_fake_binary(self) -> None:
        script = self.write_script("echo 'fake ffmpeg version 1.0'\n")
        self.assertEqual(check_ffmpeg(str(script)), "fake ffmpeg version 1.0")


if __name__ == "__main__":
    unittest.main()
