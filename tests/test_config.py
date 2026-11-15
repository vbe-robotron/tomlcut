from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from tomlcut.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.mp4"
        self.source.write_bytes(b"not really a video")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_config(self, body: str, name: str = "clips.toml") -> Path:
        path = self.root / name
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def basic_config(self, *, extra: str = "", clip_extra: str = "") -> Path:
        return self.write_config(
            f"""
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            start = "00:00:05"
            duration = 10
            name = "sub/clip.mp4"
            {clip_extra}

            {extra}
            """
        )

    def test_basic_config(self) -> None:
        config = load_config(self.basic_config())

        self.assertEqual(config.output_dir, self.root / "out")
        self.assertEqual(config.path, self.root / "clips.toml")
        self.assertTrue(config.dedupe)
        self.assertEqual(config.render.mode, "encode")
        self.assertEqual(config.render.preset, "medium")
        self.assertEqual(config.render.crf, 18)
        self.assertEqual(config.render.audio_bitrate, "192k")
        self.assertEqual(len(config.clips), 1)

        clip = config.clips[0]
        self.assertEqual(clip.source, self.source)
        self.assertEqual(clip.start, 5.0)
        self.assertEqual(clip.end, 15.0)
        self.assertEqual(clip.duration, 10.0)
        self.assertEqual(clip.name, "sub/clip.mp4")

    def test_end_instead_of_duration_and_default_start(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            end = "00:00:10"
            name = "clip.mp4"
            """
        )
        clip = load_config(path).clips[0]
        self.assertEqual(clip.start, 0.0)
        self.assertEqual(clip.end, 10.0)

    def test_clip_alias_and_copy_mode(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"
            dedupe = false

            [render]
            mode = "copy"
            ffmpeg = "custom-ffmpeg"
            extra_args = ["-metadata", "title=test"]

            [[clip]]
            source = "input.mp4"
            start = 1
            duration = 2
            name = "clip.mkv"
            extra_args = ["-an"]
            """
        )
        config = load_config(path)
        self.assertFalse(config.dedupe)
        self.assertEqual(config.render.mode, "copy")
        self.assertEqual(config.render.ffmpeg, "custom-ffmpeg")
        self.assertEqual(config.render.extra_args, ("-metadata", "title=test"))
        self.assertEqual(config.clips[0].extra_args, ("-an",))

    def test_output_dir_override(self) -> None:
        override = self.root / "elsewhere"
        config = load_config(self.basic_config(), output_dir=override)
        self.assertEqual(config.output_dir, override)

    def test_ffmpeg_override(self) -> None:
        config = load_config(self.basic_config(), ffmpeg="/usr/bin/ffmpeg")
        self.assertEqual(config.render.ffmpeg, "/usr/bin/ffmpeg")

    def test_identical_duplicate_clips_are_allowed(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            start = 1
            duration = 2
            name = "clip.mp4"

            [[clips]]
            source = "input.mp4"
            start = 1
            duration = 2
            name = "clip.mp4"
            """
        )
        config = load_config(path)
        self.assertEqual(len(config.clips), 2)

    def test_conflicting_duplicate_output_is_rejected(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            start = 1
            duration = 2
            name = "clip.mp4"

            [[clips]]
            source = "input.mp4"
            start = 5
            duration = 2
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "both write to"):
            load_config(path)

    def test_duration_and_end_are_exclusive(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            start = 1
            duration = 2
            end = 3
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "either duration or end"):
            load_config(path)

    def test_duration_or_end_required(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            start = 1
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "duration or end is required"):
            load_config(path)

    def test_invalid_ranges(self) -> None:
        cases = {
            "zero duration": "duration = 0",
            "negative duration": "duration = -1",
            "end before start": "start = 5\nend = 4",
            "negative start": "start = -1\nduration = 2",
        }
        for name, extra in cases.items():
            with self.subTest(case=name):
                path = self.write_config(
                    f"""
                    output_dir = "out"

                    [[clips]]
                    source = "input.mp4"
                    {extra}
                    name = "clip.mp4"
                    """
                )
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_unsafe_output_names(self) -> None:
        cases = ["../escape.mp4", "/tmp/escape.mp4", "clip", "sub/..", ""]
        for name in cases:
            with self.subTest(name=name):
                path = self.write_config(
                    f"""
                    output_dir = "out"

                    [[clips]]
                    source = "input.mp4"
                    duration = 1
                    name = {name!r}
                    """
                )
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_manifest_name_is_reserved(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            duration = 1
            name = ".tomlcut-manifest.json"
            """
        )
        with self.assertRaisesRegex(ConfigError, "reserved"):
            load_config(path)

    def test_missing_source(self) -> None:
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "missing.mp4"
            duration = 1
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "file not found"):
            load_config(path)

    def test_source_must_be_a_file(self) -> None:
        (self.root / "directory").mkdir()
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "directory"
            duration = 1
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "not a regular file"):
            load_config(path)

    def test_output_dir_must_not_be_a_file(self) -> None:
        (self.root / "out").write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "not a directory"):
            load_config(self.basic_config())

    def test_output_target_must_not_be_a_directory(self) -> None:
        (self.root / "out" / "clip.mp4").mkdir(parents=True)
        path = self.write_config(
            """
            output_dir = "out"

            [[clips]]
            source = "input.mp4"
            duration = 1
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "is a directory"):
            load_config(path)

    def test_unknown_keys_are_rejected(self) -> None:
        cases = [
            (
                "output_dir = 'out'\nwat = 1\n[[clips]]\nsource='input.mp4'\nduration=1\nname='c.mp4'",
                "unknown key",
            ),
            (
                "output_dir = 'out'\n[render]\nnope = 1\n[[clips]]\nsource='input.mp4'\nduration=1\nname='c.mp4'",
                "unknown key",
            ),
            (
                "output_dir = 'out'\n[[clips]]\nsource='input.mp4'\nduration=1\nname='c.mp4'\nnope=1",
                "unknown key",
            ),
        ]
        for index, (body, message) in enumerate(cases):
            with self.subTest(index=index):
                path = self.write_config(body)
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_clips_required(self) -> None:
        cases = [
            "output_dir = 'out'",
            "output_dir = 'out'\nclips = []",
            "output_dir = 'out'\n[clips]\nsource='input.mp4'",
            "output_dir = 'out'\n[[clips]]\nsource='input.mp4'\nduration=1\nname='c.mp4'\n[[clip]]\nsource='input.mp4'\nduration=1\nname='d.mp4'",
        ]
        for body in cases:
            with self.subTest(body=body):
                path = self.write_config(body)
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_invalid_option_values(self) -> None:
        cases = [
            ("dedupe = 'yes'", "dedupe"),
            ("[render]\nmode = 'fast'", "render.mode"),
            ("[render]\ncrf = 99", "render.crf"),
            ("[render]\ncrf = true", "render.crf"),
            ("[render]\npreset = ''", "render.preset"),
            ("[render]\nextra_args = [1]", "render.extra_args"),
        ]
        for extra, message in cases:
            with self.subTest(extra=extra):
                path = self.write_config(
                    f"""
                    output_dir = "out"
                    {extra}

                    [[clips]]
                    source = "input.mp4"
                    duration = 1
                    name = "clip.mp4"
                    """
                )
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_missing_output_dir(self) -> None:
        path = self.write_config(
            """
            [[clips]]
            source = "input.mp4"
            duration = 1
            name = "clip.mp4"
            """
        )
        with self.assertRaisesRegex(ConfigError, "output_dir is required"):
            load_config(path)

    def test_invalid_toml(self) -> None:
        path = self.write_config("this is not toml [")
        with self.assertRaisesRegex(ConfigError, "invalid TOML"):
            load_config(path)

    def test_missing_config_file(self) -> None:
        with self.assertRaisesRegex(ConfigError, "file not found"):
            load_config(self.root / "missing.toml")


if __name__ == "__main__":
    unittest.main()
